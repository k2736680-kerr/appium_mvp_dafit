import shutil
import subprocess
import time
import wave
from pathlib import Path

from core.config import PROJECT_ROOT


class AudioInjector:
    """
    负责把测试音频播放到 Windows 默认播放设备。
    你的环境应把默认播放设备设到 VB-CABLE，并在模拟器 Microphone 里打开
    Virtual microphone uses host audio input。
    """
    def __init__(self, project_root=None):
        self.project_root = Path(project_root or PROJECT_ROOT)

    def resolve(self, file_path):
        path = Path(file_path)
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            raise FileNotFoundError(f"音频文件不存在: {path}")
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
        print(f"[AUDIO] play {audio_file}")

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
