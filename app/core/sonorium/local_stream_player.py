"""
Local Stream Player - Plays channel streams through local audio device.

This treats the local audio output like a network speaker:
- Connects to the channel's HTTP MP3 stream
- Decodes MP3 to PCM using PyAV
- Outputs to the local audio device via sounddevice

This unifies the playback model - local and network speakers both
consume the same channel streams.
"""

from __future__ import annotations

import queue
import threading
import time
import urllib.request
import urllib.error
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import av
except ImportError:
    av = None

from sonorium.obs import logger

SAMPLE_RATE = 44100
BLOCK_SIZE = 2048  # Larger blocks = fewer callbacks = less chance of underrun
BUFFER_QUEUE_SIZE = 100  # Number of decoded audio blocks to buffer
PRE_BUFFER_BLOCKS = 30  # Fill this many blocks before starting playback (~1.4 seconds)


class LocalStreamPlayer:
    """
    Plays an HTTP audio stream through the local audio device.

    Acts like a network speaker - connects to /stream/channel{n} and plays it locally.
    """

    def __init__(self, device_id: int | str | None = None):
        """
        Initialize the local stream player.

        Args:
            device_id: Specific audio device ID or name. None for default device.
        """
        if sd is None:
            raise RuntimeError("sounddevice not installed. Run: pip install sounddevice")
        if av is None:
            raise RuntimeError("av (PyAV) not installed. Run: pip install av")

        self.device_id = device_id
        self._stream: Optional[sd.OutputStream] = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=BUFFER_QUEUE_SIZE)
        self._running = False
        self._fetch_thread: Optional[threading.Thread] = None
        self._current_url: Optional[str] = None
        self._current_channel_id: Optional[int] = None
        self._volume = 1.0
        self._lock = threading.Lock()
        self._prebuffering = True  # True until we've buffered enough data

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.0, min(1.0, value))

    @property
    def is_playing(self) -> bool:
        return self._running and self._current_url is not None

    @property
    def current_channel_id(self) -> Optional[int]:
        return self._current_channel_id

    def _audio_callback(self, outdata: np.ndarray, frames: int, time_info, status):
        """Callback for sounddevice output stream."""
        if status:
            logger.warning(f"LocalStreamPlayer audio callback status: {status}")

        # During pre-buffering, output silence
        if self._prebuffering:
            outdata.fill(0)
            return

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

    def _fetch_and_decode_loop(self, url: str):
        """
        Background thread that fetches MP3 stream and decodes to PCM.

        This mimics what a network speaker does - connects to the HTTP stream
        and decodes audio in real-time.
        """
        logger.info(f"LocalStreamPlayer: Starting stream fetch from {url}")

        retry_count = 0
        max_retries = 5
        retry_delay = 1.0

        while self._running:
            try:
                # Open HTTP stream
                request = urllib.request.Request(url)
                request.add_header('User-Agent', 'Sonorium-LocalPlayer/1.0')
                request.add_header('Accept', 'audio/mpeg')

                with urllib.request.urlopen(request, timeout=10) as response:
                    logger.info(f"LocalStreamPlayer: Connected to stream (status {response.status})")
                    retry_count = 0  # Reset on successful connect

                    # Create PyAV container for MP3 decoding
                    # We need to wrap the HTTP response in a way PyAV can read
                    container = av.open(response, format='mp3', mode='r')

                    try:
                        audio_stream = container.streams.audio[0]

                        # Create resampler to ensure consistent output format
                        resampler = av.AudioResampler(
                            format='s16',
                            layout='stereo',
                            rate=SAMPLE_RATE
                        )

                        for frame in container.decode(audio=0):
                            if not self._running:
                                break

                            # Resample to our target format
                            resampled_frames = resampler.resample(frame)

                            for resampled in resampled_frames:
                                if not self._running:
                                    break

                                # Convert to numpy array
                                audio_array = resampled.to_ndarray()

                                # Shape is (channels, samples), transpose to (samples, channels)
                                if audio_array.ndim > 1:
                                    audio_array = audio_array.T

                                # Convert int16 to float32
                                audio_float = audio_array.astype(np.float32) / 32768.0

                                # Ensure stereo
                                if audio_float.ndim == 1:
                                    audio_float = np.column_stack([audio_float, audio_float])
                                elif audio_float.shape[1] == 1:
                                    audio_float = np.column_stack([audio_float.flatten(), audio_float.flatten()])

                                # Split into blocks and queue
                                for i in range(0, len(audio_float), BLOCK_SIZE):
                                    if not self._running:
                                        break

                                    block = audio_float[i:i + BLOCK_SIZE]
                                    if len(block) < BLOCK_SIZE:
                                        block = np.pad(block, ((0, BLOCK_SIZE - len(block)), (0, 0)))

                                    try:
                                        # Use timeout to allow checking _running flag
                                        self._audio_queue.put(block, timeout=0.1)

                                        # Check if pre-buffering is complete
                                        if self._prebuffering and self._audio_queue.qsize() >= PRE_BUFFER_BLOCKS:
                                            logger.info(f"LocalStreamPlayer: Pre-buffer complete ({self._audio_queue.qsize()} blocks), starting playback")
                                            self._prebuffering = False

                                    except queue.Full:
                                        # Drop old data to keep stream current
                                        try:
                                            self._audio_queue.get_nowait()
                                            self._audio_queue.put(block, block=False)
                                        except queue.Empty:
                                            pass

                    finally:
                        container.close()

            except urllib.error.HTTPError as e:
                logger.error(f"LocalStreamPlayer: HTTP error {e.code}: {e.reason}")
                if e.code == 404:
                    # Channel not active, wait and retry
                    retry_count += 1
                    if retry_count > max_retries:
                        logger.error(f"LocalStreamPlayer: Max retries reached, stopping")
                        break
                    time.sleep(retry_delay)
                else:
                    break

            except urllib.error.URLError as e:
                logger.error(f"LocalStreamPlayer: URL error: {e.reason}")
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"LocalStreamPlayer: Max retries reached, stopping")
                    break
                time.sleep(retry_delay)

            except av.error.EOFError:
                # Stream ended, try to reconnect
                logger.info("LocalStreamPlayer: Stream ended, reconnecting...")
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"LocalStreamPlayer: Error in fetch loop: {e}")
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"LocalStreamPlayer: Max retries reached, stopping")
                    break
                time.sleep(retry_delay)

        logger.info("LocalStreamPlayer: Fetch loop ended")

    def play(self, stream_url: str, channel_id: int):
        """
        Start playing an audio stream.

        Args:
            stream_url: Full URL to the channel stream (e.g., http://127.0.0.1:8008/stream/channel1)
            channel_id: The channel ID being played
        """
        # Stop any existing playback
        if self._running:
            self.stop()

        with self._lock:
            self._current_url = stream_url
            self._current_channel_id = channel_id
            self._running = True
            self._prebuffering = True  # Start in pre-buffering mode

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

            # Start fetch thread
            self._fetch_thread = threading.Thread(
                target=self._fetch_and_decode_loop,
                args=(stream_url,),
                daemon=True
            )
            self._fetch_thread.start()

        logger.info(f"LocalStreamPlayer: Playing channel {channel_id} from {stream_url}")

    def stop(self):
        """Stop playback."""
        with self._lock:
            self._running = False
            self._current_url = None
            channel_id = self._current_channel_id
            self._current_channel_id = None

            # Stop audio stream
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as e:
                    logger.warning(f"LocalStreamPlayer: Error stopping audio stream: {e}")
                self._stream = None

            # Wait for fetch thread
            if self._fetch_thread:
                self._fetch_thread.join(timeout=2.0)
                self._fetch_thread = None

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


def play_local(stream_url: str, channel_id: int, volume: float = 1.0):
    """
    Convenience function to play a channel stream locally.

    Args:
        stream_url: Full URL to the channel stream
        channel_id: The channel ID
        volume: Playback volume (0.0 to 1.0)
    """
    player = get_local_player()
    player.volume = volume
    player.play(stream_url, channel_id)


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
