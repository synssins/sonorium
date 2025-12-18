"""
Local Stream Player - Plays channel audio through local audio device.

Reads PCM directly from the channel's broadcast buffer (no HTTP/MP3 overhead).
This provides low-latency local playback while keeping the unified channel model.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional, TYPE_CHECKING

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.core.channel import Channel

SAMPLE_RATE = 44100
BLOCK_SIZE = 1024  # Samples per audio callback
BUFFER_QUEUE_SIZE = 100  # Number of blocks to buffer


class LocalStreamPlayer:
    """
    Plays audio from a Channel through the local audio device.

    Reads PCM directly from the channel's broadcast buffer - no HTTP or MP3
    encoding/decoding overhead. This gives smooth, low-latency local playback.
    """

    def __init__(self, device_id: int | str | None = None):
        """
        Initialize the local stream player.

        Args:
            device_id: Specific audio device ID or name. None for default device.
        """
        if sd is None:
            raise RuntimeError("sounddevice not installed. Run: pip install sounddevice")

        self.device_id = device_id
        self._stream: Optional[sd.OutputStream] = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=BUFFER_QUEUE_SIZE)
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._current_channel: Optional[Channel] = None
        self._current_channel_id: Optional[int] = None
        self._volume = 1.0
        self._lock = threading.Lock()

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.0, min(1.0, value))

    @property
    def is_playing(self) -> bool:
        return self._running and self._current_channel is not None

    @property
    def current_channel_id(self) -> Optional[int]:
        return self._current_channel_id

    def _audio_callback(self, outdata: np.ndarray, frames: int, time_info, status):
        """Callback for sounddevice output stream."""
        if status:
            logger.warning(f"LocalStreamPlayer audio callback status: {status}")

        try:
            data = self._audio_queue.get_nowait()
            # Ensure correct shape
            if len(data) < frames:
                data = np.pad(data, ((0, frames - len(data)), (0, 0)))
            elif len(data) > frames:
                data = data[:frames]

            # Apply volume
            outdata[:] = (data * self._volume).astype(np.float32)
        except queue.Empty:
            # Output silence if buffer is empty
            outdata.fill(0)

    def _channel_reader_loop(self, channel: 'Channel'):
        """
        Background thread that reads PCM from channel's broadcast buffer.

        This reads raw int16 PCM directly - no HTTP, no MP3 encoding/decoding.
        Much lower latency and CPU usage than the stream approach.
        """
        logger.info(f"LocalStreamPlayer: Starting direct read from channel {channel.id}")

        last_sequence = -1

        while self._running:
            try:
                # Wait for new data from channel
                if not channel.wait_for_data(timeout=0.1):
                    continue

                # Get new chunks since our last read
                new_chunks = channel.get_chunks_since(last_sequence)

                for seq, chunk in new_chunks:
                    if not self._running:
                        break

                    last_sequence = seq

                    # Chunk is int16 numpy array, convert to float32 for sounddevice
                    # Shape should be (samples,) for mono or (samples, channels) for stereo
                    audio_float = chunk.astype(np.float32) / 32768.0

                    # Ensure stereo
                    if audio_float.ndim == 1:
                        audio_float = np.column_stack([audio_float, audio_float])
                    elif audio_float.ndim == 2 and audio_float.shape[1] == 1:
                        audio_float = np.column_stack([audio_float.flatten(), audio_float.flatten()])

                    # Split into blocks matching our output block size
                    for i in range(0, len(audio_float), BLOCK_SIZE):
                        if not self._running:
                            break

                        block = audio_float[i:i + BLOCK_SIZE]
                        if len(block) < BLOCK_SIZE:
                            block = np.pad(block, ((0, BLOCK_SIZE - len(block)), (0, 0)))

                        try:
                            self._audio_queue.put(block, timeout=0.05)
                        except queue.Full:
                            # Drop oldest to stay current
                            try:
                                self._audio_queue.get_nowait()
                                self._audio_queue.put_nowait(block)
                            except queue.Empty:
                                pass

            except Exception as e:
                logger.error(f"LocalStreamPlayer: Error reading from channel: {e}")
                time.sleep(0.1)

        logger.info("LocalStreamPlayer: Reader loop ended")

    def play(self, channel: 'Channel'):
        """
        Start playing audio from a channel.

        Args:
            channel: The Channel object to read audio from
        """
        # Stop any existing playback
        if self._running:
            self.stop()

        with self._lock:
            self._current_channel = channel
            self._current_channel_id = channel.id
            self._running = True

            # Clear any old audio data
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break

            # Start audio output stream
            try:
                self._stream = sd.OutputStream(
                    samplerate=SAMPLE_RATE,
                    channels=2,
                    dtype=np.float32,
                    blocksize=BLOCK_SIZE,
                    device=self.device_id,
                    callback=self._audio_callback
                )
                self._stream.start()
            except Exception as e:
                logger.error(f"LocalStreamPlayer: Failed to start audio output: {e}")
                self._running = False
                raise

            # Start reader thread
            self._reader_thread = threading.Thread(
                target=self._channel_reader_loop,
                args=(channel,),
                daemon=True
            )
            self._reader_thread.start()

        logger.info(f"LocalStreamPlayer: Playing from channel {channel.id}")

    def stop(self):
        """Stop playback."""
        with self._lock:
            self._running = False
            channel_id = self._current_channel_id
            self._current_channel = None
            self._current_channel_id = None

            # Stop audio stream
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as e:
                    logger.warning(f"LocalStreamPlayer: Error stopping audio stream: {e}")
                self._stream = None

            # Wait for reader thread
            if self._reader_thread:
                self._reader_thread.join(timeout=2.0)
                self._reader_thread = None

            # Clear buffer
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break

        if channel_id is not None:
            logger.info(f"LocalStreamPlayer: Stopped playback of channel {channel_id}")
        else:
            logger.info("LocalStreamPlayer: Stopped")


# Global instance for the application
_local_player: Optional[LocalStreamPlayer] = None


def get_local_player() -> LocalStreamPlayer:
    """Get the global LocalStreamPlayer instance."""
    global _local_player
    if _local_player is None:
        _local_player = LocalStreamPlayer()
    return _local_player


def play_local(channel: 'Channel', volume: float = 1.0):
    """
    Convenience function to play a channel locally.

    Args:
        channel: The Channel object to play from
        volume: Playback volume (0.0 to 1.0)
    """
    player = get_local_player()
    player.volume = volume
    player.play(channel)


def stop_local():
    """Convenience function to stop local playback."""
    player = get_local_player()
    player.stop()


def set_local_volume(volume: float):
    """Set the local playback volume."""
    player = get_local_player()
    player.volume = volume


def is_local_playing() -> bool:
    """Check if local playback is active."""
    global _local_player
    if _local_player is None:
        return False
    return _local_player.is_playing


def get_local_channel_id() -> Optional[int]:
    """Get the channel ID currently playing locally."""
    global _local_player
    if _local_player is None:
        return None
    return _local_player.current_channel_id
