import shutil
import subprocess
import time
import wave
from pathlib import Path

from core.config import PROJECT_ROOT


class AudioInjector:
    """
    Plays test audio through the Windows playback device.

    The environment should route Windows playback to VB-CABLE, and the Android
    emulator should enable host microphone access so the app records that audio.
    """

    def __init__(self, project_root=None):
        self.project_root = Path(project_root or PROJECT_ROOT)

    def resolve(self, file_path):
        path = Path(file_path)
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            raise FileNotFoundError(f"Audio file does not exist: {path}")
        return path

    def resolve_with_fallback(self, file_path, fallback_file=None):
        try:
            return self.resolve(file_path)
        except FileNotFoundError:
            if not fallback_file:
                raise
            return self.resolve(fallback_file)

    def duration_ms(self, file_path):
        audio_file = self.resolve(file_path)
        if audio_file.suffix.lower() != ".wav":
            return None

        with wave.open(str(audio_file), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            if rate <= 0:
                return None
            return int(frames * 1000 / rate)

    def play(self, file_path, wait_after=1, fallback_file=None):
        audio_file = self.resolve_with_fallback(file_path, fallback_file=fallback_file)
        print(f"[AUDIO] play {audio_file} mode=host")

        if shutil.which("ffplay"):
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(audio_file)],
                check=True,
            )
        else:
            ps = f'$player = New-Object System.Media.SoundPlayer "{audio_file}"; $player.PlaySync()'
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                check=True,
            )

        time.sleep(wait_after)
