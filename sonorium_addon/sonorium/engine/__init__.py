"""
Sonorium's playback engine on its own: a theme folder (plus a preset,
playback values and a seed) in, mono or stereo PCM out, pulled by the caller. No web
server, Home Assistant, MQTT, speakers or MP3 encoder; no threads.
Sequences, playlists and theme crossfades run as on a server channel,
timed in audio rendered (start_sequence, start_playlist, set_theme, status).

    from sonorium.engine import Engine, PyAvDecoder

    eng = Engine("themes/Rain", preset_id=None, seed=1234, decoder=PyAvDecoder())
    pcm = eng.render(44100)   # numpy int16, mono, 44100 Hz
    eng.set_channels(2)       # stereo from here: render() gives (n, 2)
    eng.close()

See docs/ENGINE.md. Third-party imports: numpy; PyAV (`av`) only when
PyAvDecoder opens a file.
"""
from sonorium.decoders import (MONO, STEREO, DecodedAudio, Decoder, PcmFileDecoder, PyAvDecoder, cache_lookup,
                               current_channels, current_decoder, fold_to_mono, mirror_lookup, set_default_decoder,
                               to_channels, use_channels, use_decoder)
from sonorium.engine.engine import PRESET_GLIDE_SECONDS, Engine, SampleClock
from sonorium.engine.theme_loader import DEFAULT_MASTER_GAIN, ThemeFolder, load_theme
from sonorium.recording import BLOCK_SAMPLES, SAMPLE_RATE

__all__ = [
    "Engine", "SampleClock", "Decoder", "DecodedAudio", "PyAvDecoder", "PcmFileDecoder", "mirror_lookup",
    "cache_lookup", "current_decoder", "set_default_decoder", "use_decoder", "load_theme", "ThemeFolder",
    "MONO", "STEREO", "current_channels", "use_channels", "fold_to_mono", "to_channels",
    "SAMPLE_RATE", "BLOCK_SAMPLES", "DEFAULT_MASTER_GAIN", "PRESET_GLIDE_SECONDS",
]
