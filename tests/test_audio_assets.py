import wave
from pathlib import Path


def test_source_audio_assets_are_valid_wav_files():
    audio_files = sorted(Path("assets/audio").glob("source_*.wav"))
    assert audio_files

    for audio_file in audio_files:
        with wave.open(str(audio_file), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() > 0
            assert wav.getnframes() / wav.getframerate() > 1
