"""
Local audio output module for standalone Sonorium.

Uses sounddevice to output mixed audio directly to the system's audio device.
"""

from __future__ import annotations

import threading
import queue
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

from sonorium.obs import logger

SAMPLE_RATE = 44100
CHANNELS = 2  # Stereo output
BLOCK_SIZE = 1024
BUFFER_SIZE = 8  # Number of blocks to buffer


class AudioOutputDevice:
    """Manages audio output to a local audio device."""

    def __init__(self, device_id: int | str | None = None):
        """
        Initialize audio output.

        Args:
            device_id: Specific device ID or name. None for default device.
        """
        if sd is None:
            raise RuntimeError("sounddevice library not installed. Run: pip install sounddevice")

        self.device_id = device_id
        self._stream: sd.OutputStream | None = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=BUFFER_SIZE * 4)
        self._running = False
        self._thread: threading.Thread | None = None
        self._volume = 1.0

    @staticmethod
    def list_devices() -> list[dict]:
        """List available audio output devices."""
        if sd is None:
            return []

        devices = []
        for i, dev in enumerate(sd.query_devices()):
            if dev['max_output_channels'] > 0:
                devices.append({
                    'id': i,
                    'name': dev['name'],
                    'channels': dev['max_output_channels'],
                    'sample_rate': dev['default_samplerate'],
                    'is_default': i == sd.default.device[1]
                })
        return devices

    @staticmethod
    def get_default_device() -> dict | None:
        """Get the default output device."""
        if sd is None:
            return None

        try:
            device_id = sd.default.device[1]
            dev = sd.query_devices(device_id)
            return {
                'id': device_id,
                'name': dev['name'],
                'channels': dev['max_output_channels'],
                'sample_rate': dev['default_samplerate'],
                'is_default': True
            }
        except Exception as e:
            logger.error(f"Error getting default device: {e}")
            return None

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.0, min(1.0, value))

    def _audio_callback(self, outdata: np.ndarray, frames: int, time_info, status):
        """Callback for sounddevice stream."""
        if status:
            logger.warning(f"Audio callback status: {status}")

        try:
            data = self._audio_queue.get_nowait()
            # Ensure correct shape and apply volume
            if data.shape[0] != frames:
                # Resize if needed
                if len(data) < frames:
                    data = np.pad(data, ((0, frames - len(data)), (0, 0)))
                else:
                    data = data[:frames]

            outdata[:] = (data * self._volume).astype(np.float32)
        except queue.Empty:
            # Output silence if no data
            outdata.fill(0)

    def start(self):
        """Start audio output stream."""
        if self._running:
            return

        try:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=np.float32,
                blocksize=BLOCK_SIZE,
                device=self.device_id,
                callback=self._audio_callback
            )
            self._stream.start()
            self._running = True
            logger.info(f"Audio output started on device: {self.device_id or 'default'}")
        except Exception as e:
            logger.error(f"Failed to start audio output: {e}")
            raise

    def stop(self):
        """Stop audio output stream."""
        self._running = False

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.warning(f"Error stopping stream: {e}")
            self._stream = None

        # Clear the queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        logger.info("Audio output stopped")

    def write(self, audio_data: np.ndarray):
        """
        Write audio data to the output buffer.

        Args:
            audio_data: Audio samples as numpy array.
                       Shape should be (samples,) for mono or (samples, 2) for stereo.
                       Values should be float32 in range [-1.0, 1.0] or int16.
        """
        if not self._running:
            return

        # Convert int16 to float32 if needed
        if audio_data.dtype == np.int16:
            audio_data = audio_data.astype(np.float32) / 32768.0

        # Ensure stereo
        if audio_data.ndim == 1:
            audio_data = np.column_stack([audio_data, audio_data])
        elif audio_data.shape[1] == 1:
            audio_data = np.column_stack([audio_data.flatten(), audio_data.flatten()])

        # Split into blocks and queue
        for i in range(0, len(audio_data), BLOCK_SIZE):
            block = audio_data[i:i + BLOCK_SIZE]
            if len(block) < BLOCK_SIZE:
                block = np.pad(block, ((0, BLOCK_SIZE - len(block)), (0, 0)))

            try:
                self._audio_queue.put(block, block=False)
            except queue.Full:
                # Drop oldest block if queue is full
                try:
                    self._audio_queue.get_nowait()
                    self._audio_queue.put(block, block=False)
                except queue.Empty:
                    pass

    @property
    def is_running(self) -> bool:
        return self._running


class StreamInfo:
    """Holds stream and its current fade state."""
    def __init__(self, generator, volume: float = 1.0, group: str = "default"):
        self.generator = generator
        self.volume = volume  # Current volume multiplier (0.0 to 1.0)
        self.target_volume = volume  # Target volume for fading
        self.fade_rate = 0.0  # Volume change per block (0 = no fade)
        self.group = group  # Group identifier for batch operations
        self.marked_for_removal = False  # Remove after fade completes
        self.error_count = 0  # Track consecutive errors for resilience


class AudioMixer:
    """
    Mixes multiple audio streams and outputs to a device.

    This replaces the HTTP streaming model - instead of streaming to
    media_player entities, we mix locally and output to the system audio.
    """

    # Crossfade duration in seconds
    CROSSFADE_DURATION = 2.0

    def __init__(self, output_device: AudioOutputDevice | None = None):
        self._output = output_device or AudioOutputDevice()
        self._streams: dict[str, StreamInfo] = {}  # stream_id -> StreamInfo
        self._running = False
        self._mix_thread: threading.Thread | None = None
        self._master_volume = 1.0
        self._lock = threading.Lock()  # Protect stream modifications

    @property
    def master_volume(self) -> float:
        return self._master_volume

    @master_volume.setter
    def master_volume(self, value: float):
        self._master_volume = max(0.0, min(1.0, value))
        self._output.volume = self._master_volume

    def add_stream(self, stream_id: str, stream_generator, group: str = "default", fade_in: bool = False):
        """Add a stream to the mix.

        Args:
            stream_id: Unique identifier for this stream
            stream_generator: Generator yielding audio chunks
            group: Group identifier for batch operations (e.g., theme name)
            fade_in: If True, fade in from small volume to 1.0 over CROSSFADE_DURATION
        """
        # Pre-fetch first chunk to avoid latency when stream is first read in mix loop
        # This ensures the stream is "primed" and ready to produce audio immediately
        try:
            first_chunk = next(stream_generator)
        except StopIteration:
            logger.warning(f"Stream {stream_id} produced no audio, not adding")
            return
        except Exception as e:
            logger.error(f"Error priming stream {stream_id}: {e}")
            return

        # Create a wrapper generator that yields the pre-fetched chunk first
        def primed_generator():
            yield first_chunk
            yield from stream_generator

        # Start at small volume (not zero) to avoid complete silence on first frame
        # This provides a smoother fade-in start
        initial_volume = 0.02 if fade_in else 1.0
        stream_info = StreamInfo(primed_generator(), volume=initial_volume, group=group)

        if fade_in:
            stream_info.target_volume = 1.0
            # Calculate fade rate: volume change per block
            blocks_for_fade = (self.CROSSFADE_DURATION * SAMPLE_RATE) / BLOCK_SIZE
            stream_info.fade_rate = (1.0 - initial_volume) / blocks_for_fade

        with self._lock:
            self._streams[stream_id] = stream_info
        logger.info(f"Added stream: {stream_id} (group={group}, fade_in={fade_in})")

    def remove_stream(self, stream_id: str, fade_out: bool = False):
        """Remove a stream from the mix.

        Args:
            stream_id: Stream to remove
            fade_out: If True, fade out before removing
        """
        with self._lock:
            if stream_id not in self._streams:
                return

            if fade_out:
                stream_info = self._streams[stream_id]
                stream_info.target_volume = 0.0
                blocks_for_fade = (self.CROSSFADE_DURATION * SAMPLE_RATE) / BLOCK_SIZE
                stream_info.fade_rate = -1.0 / blocks_for_fade
                stream_info.marked_for_removal = True
                logger.info(f"Fading out stream: {stream_id}")
            else:
                del self._streams[stream_id]
                logger.info(f"Removed stream: {stream_id}")

    def fade_out_group(self, group: str):
        """Fade out all streams in a group."""
        with self._lock:
            blocks_for_fade = (self.CROSSFADE_DURATION * SAMPLE_RATE) / BLOCK_SIZE
            fade_rate = -1.0 / blocks_for_fade

            for stream_id, stream_info in self._streams.items():
                if stream_info.group == group:
                    stream_info.target_volume = 0.0
                    stream_info.fade_rate = fade_rate
                    stream_info.marked_for_removal = True
            logger.info(f"Fading out group: {group}")

    def clear_streams(self, fade_out: bool = False):
        """Remove all streams.

        Args:
            fade_out: If True, fade out all streams before clearing
        """
        with self._lock:
            if fade_out:
                blocks_for_fade = (self.CROSSFADE_DURATION * SAMPLE_RATE) / BLOCK_SIZE
                fade_rate = -1.0 / blocks_for_fade

                for stream_info in self._streams.values():
                    stream_info.target_volume = 0.0
                    stream_info.fade_rate = fade_rate
                    stream_info.marked_for_removal = True
                logger.info("Fading out all streams")
            else:
                self._streams.clear()
                logger.info("Cleared all streams")

    def get_stream_ids(self) -> list[str]:
        """Get list of current stream IDs."""
        with self._lock:
            return list(self._streams.keys())

    def has_stream(self, stream_id: str) -> bool:
        """Check if a stream exists."""
        with self._lock:
            return stream_id in self._streams

    def set_stream_volume(self, stream_id: str, volume: float, fade: bool = True):
        """Set the volume of an existing stream.

        Args:
            stream_id: Stream to update
            volume: Target volume (0.0 to 1.0)
            fade: If True, fade to the new volume; if False, set immediately
        """
        with self._lock:
            if stream_id not in self._streams:
                return False

            stream_info = self._streams[stream_id]
            volume = max(0.0, min(1.0, volume))

            if fade:
                stream_info.target_volume = volume
                blocks_for_fade = (self.CROSSFADE_DURATION * SAMPLE_RATE) / BLOCK_SIZE
                if volume > stream_info.volume:
                    stream_info.fade_rate = (volume - stream_info.volume) / blocks_for_fade
                else:
                    stream_info.fade_rate = (volume - stream_info.volume) / blocks_for_fade
                # Don't mark for removal - this is just a volume change
                stream_info.marked_for_removal = False
            else:
                stream_info.volume = volume
                stream_info.target_volume = volume
                stream_info.fade_rate = 0

            logger.debug(f"Set stream {stream_id} volume to {volume} (fade={fade})")
            return True

    def _mix_loop(self):
        """Main mixing loop - runs in separate thread."""
        import time

        logger.info("Mix loop started")

        # Calculate time per block for proper pacing
        block_duration = BLOCK_SIZE / SAMPLE_RATE  # seconds per block
        next_block_time = time.perf_counter()
        loop_error_count = 0

        while self._running:
            try:
                # Wait until it's time to produce the next block
                now = time.perf_counter()
                sleep_time = next_block_time - now
                if sleep_time > 0:
                    time.sleep(sleep_time)

                # Schedule next block
                next_block_time += block_duration

                # If we've fallen behind, catch up
                if next_block_time < time.perf_counter():
                    next_block_time = time.perf_counter() + block_duration

                # Get a snapshot of streams under lock (minimize lock time)
                with self._lock:
                    if not self._streams:
                        # No streams - output silence (quick operation, ok under lock)
                        silence = np.zeros((BLOCK_SIZE, 2), dtype=np.float32)
                        self._output.write(silence)
                        loop_error_count = 0
                        continue
                    # Copy stream info references - the StreamInfo objects are still shared
                    # but we won't hold the lock while reading from generators
                    active_streams = list(self._streams.items())

                # Process streams OUTSIDE the lock to avoid blocking other operations
                chunks = []
                dead_streams = []

                for stream_id, stream_info in active_streams:
                    try:
                        chunk = next(stream_info.generator)
                        # Convert to float32 if int16
                        if chunk.dtype == np.int16:
                            chunk = chunk.astype(np.float32) / 32768.0
                        # Flatten if needed
                        if chunk.ndim > 1:
                            chunk = chunk.flatten()

                        # Apply stream volume (for fading)
                        chunk = chunk * stream_info.volume

                        # Always add the chunk first (even if fading out)
                        chunks.append(chunk)

                        # Reset error count on successful read
                        stream_info.error_count = 0

                        # Update fade (atomic operations on StreamInfo, safe without lock)
                        if stream_info.fade_rate != 0:
                            stream_info.volume += stream_info.fade_rate
                            # Clamp and check if fade complete
                            if stream_info.fade_rate > 0 and stream_info.volume >= stream_info.target_volume:
                                stream_info.volume = stream_info.target_volume
                                stream_info.fade_rate = 0
                            elif stream_info.fade_rate < 0 and stream_info.volume <= stream_info.target_volume:
                                stream_info.volume = stream_info.target_volume
                                stream_info.fade_rate = 0
                                if stream_info.marked_for_removal:
                                    dead_streams.append(stream_id)

                    except StopIteration:
                        dead_streams.append(stream_id)
                        logger.info(f"Stream {stream_id} ended (StopIteration)")
                    except Exception as e:
                        # Track errors per stream - only kill after multiple consecutive failures
                        stream_info.error_count = getattr(stream_info, 'error_count', 0) + 1
                        if stream_info.error_count >= 10:
                            logger.error(f"Stream {stream_id} failed after {stream_info.error_count} errors: {e}")
                            dead_streams.append(stream_id)
                        elif stream_info.error_count == 1:
                            logger.warning(f"Error reading stream {stream_id}: {e} (will retry)")

                # Remove dead streams (need lock for dict modification)
                if dead_streams:
                    with self._lock:
                        for stream_id in dead_streams:
                            if stream_id in self._streams:
                                del self._streams[stream_id]
                                logger.debug(f"Removed finished stream: {stream_id}")

                if chunks:
                    # Mix all chunks together with normalization
                    # Use sqrt(n) normalization to prevent clipping while maintaining volume
                    max_len = max(len(c) for c in chunks)
                    mixed = np.zeros(max_len, dtype=np.float32)

                    for chunk in chunks:
                        if len(chunk) < max_len:
                            chunk = np.pad(chunk, (0, max_len - len(chunk)))
                        mixed += chunk

                    # Normalize by effective number of streams (weighted by volume)
                    # This prevents volume dips during crossfades when streams are at partial volume
                    # During crossfade: old streams fade out (volume decreasing), new streams fade in (volume increasing)
                    # Sum of volumes stays roughly constant, so normalization factor stays stable
                    effective_streams = sum(
                        stream_info.volume for _, stream_info in active_streams
                        if not stream_info.marked_for_removal or stream_info.volume > 0.01
                    )

                    # Use effective streams for normalization, with a minimum of 1.0
                    if effective_streams > 1.0:
                        mixed /= np.sqrt(effective_streams)
                    elif len(chunks) > 1:
                        # Fallback: if effective is very low but we have multiple chunks,
                        # use a gentler normalization to avoid sudden volume jumps
                        mixed /= np.sqrt(max(1.0, effective_streams))

                    # Clip to prevent distortion
                    mixed = np.clip(mixed, -1.0, 1.0)

                    # Convert to stereo
                    stereo = np.column_stack([mixed, mixed])
                    self._output.write(stereo)
                else:
                    # No active chunks - output silence
                    silence = np.zeros((BLOCK_SIZE, 2), dtype=np.float32)
                    self._output.write(silence)

                # Reset loop error count on successful iteration
                loop_error_count = 0

            except Exception as e:
                loop_error_count += 1
                if loop_error_count == 1 or loop_error_count % 100 == 0:
                    logger.error(f"Mix loop error (count={loop_error_count}): {e}")
                # Output silence on error to keep audio stream alive
                try:
                    silence = np.zeros((BLOCK_SIZE, 2), dtype=np.float32)
                    self._output.write(silence)
                except Exception:
                    pass
                # Brief sleep to prevent tight error loop
                time.sleep(0.01)

        logger.info("Mix loop stopped")

    def start(self):
        """Start the mixer."""
        if self._running:
            return

        self._output.start()
        self._running = True
        self._mix_thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._mix_thread.start()
        logger.info("AudioMixer started")

    def stop(self):
        """Stop the mixer."""
        self._running = False

        if self._mix_thread:
            self._mix_thread.join(timeout=2.0)
            self._mix_thread = None

        self._output.stop()
        self.clear_streams()
        logger.info("AudioMixer stopped")

    @property
    def is_running(self) -> bool:
        return self._running
