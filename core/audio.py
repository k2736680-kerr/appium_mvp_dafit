import hashlib
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path

from core.config import PROJECT_ROOT
from core.config import (
    ANDROID_ADB,
    ANDROID_ADB_SERIAL,
    AUDIO_INJECTION_MODE,
    DEVICE_FARM_DOCKER_COMMAND,
    DEVICE_FARM_EMULATOR_CONTAINER,
    DEVICE_FARM_PULSE_SERVER,
    DEVICE_FARM_PULSE_SINK,
    DEVICE_FARM_SSH_TARGET,
)


SAFE_CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class AudioInjector:
    """
    Plays test audio through the configured injection backend.

    The environment should route Windows playback to VB-CABLE, and the Android
    emulator should enable host microphone access so the app records that audio.
    """

    def __init__(
        self,
        project_root=None,
        mode=None,
        docker_command=None,
        farm_container=None,
        pulse_server=None,
        pulse_sink=None,
        ssh_target=None,
    ):
        self.project_root = Path(project_root or PROJECT_ROOT)
        self.mode = str(mode or AUDIO_INJECTION_MODE).strip().lower()
        self.docker_command = str(docker_command or DEVICE_FARM_DOCKER_COMMAND).strip()
        self.farm_container = str(farm_container or DEVICE_FARM_EMULATOR_CONTAINER).strip()
        self.pulse_server = str(pulse_server or DEVICE_FARM_PULSE_SERVER).strip()
        self.pulse_sink = str(pulse_sink or DEVICE_FARM_PULSE_SINK).strip()
        self.ssh_target = str(ssh_target or DEVICE_FARM_SSH_TARGET).strip()

    def prepare(self):
        """Reset the local emulator microphone bridge before an audio case.

        The Windows DirectSound input can remain logically enabled while the
        emulator delivers silence to Android. Toggling host mic routing before
        Auro opens AudioRecord restores the VB-CABLE path without changing AVD
        data or the signed-in app session.
        """
        if self.mode != "host":
            return
        if not ANDROID_ADB_SERIAL or not ANDROID_ADB_SERIAL.startswith("emulator-"):
            return

        base = [ANDROID_ADB, "-s", ANDROID_ADB_SERIAL, "emu", "avd"]
        for command in ("hostmicoff", "hostmicon"):
            subprocess.run([*base, command], check=True)
            time.sleep(0.5)

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
        print(f"[AUDIO] play {audio_file} mode={self.mode}")

        if self.mode == "docker_pulse":
            self.play_via_device_farm(audio_file)
        elif self.mode == "ssh_docker_pulse":
            self.play_via_ssh_device_farm(audio_file)
        elif self.mode == "host":
            self.play_on_host(audio_file)
        else:
            raise ValueError(f"Unsupported audio injection mode: {self.mode}")

        time.sleep(wait_after)

    def play_on_host(self, audio_file):
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

    def play_via_device_farm(self, audio_file):
        if not self.docker_command:
            raise RuntimeError("DEVICE_FARM_DOCKER_COMMAND is required for docker_pulse mode")
        if not SAFE_CONTAINER_PATTERN.fullmatch(self.farm_container):
            raise RuntimeError(
                "DEVICE_FARM_EMULATOR_CONTAINER must be a concrete managed container name"
            )
        if not self.pulse_server or not self.pulse_sink:
            raise RuntimeError("Device Farm PulseAudio server and sink are required")

        digest = hashlib.sha256(audio_file.read_bytes()).hexdigest()[:16]
        remote_file = f"/tmp/alcor-audio-{digest}{audio_file.suffix.lower()}"
        subprocess.run(
            [
                self.docker_command,
                "cp",
                str(audio_file),
                f"{self.farm_container}:{remote_file}",
            ],
            check=True,
        )
        subprocess.run(
            [
                self.docker_command,
                "exec",
                self.farm_container,
                "env",
                f"PULSE_SERVER={self.pulse_server}",
                "paplay",
                f"--device={self.pulse_sink}",
                remote_file,
            ],
            check=True,
        )

    def play_via_ssh_device_farm(self, audio_file):
        """Stream audio to the managed farm host without requiring local Docker."""
        if not self.ssh_target:
            raise RuntimeError("DEVICE_FARM_SSH_TARGET is required for ssh_docker_pulse mode")
        if not SAFE_CONTAINER_PATTERN.fullmatch(self.farm_container):
            raise RuntimeError(
                "DEVICE_FARM_EMULATOR_CONTAINER must be a concrete managed container name"
            )
        if not self.pulse_server or not self.pulse_sink:
            raise RuntimeError("Device Farm PulseAudio server and sink are required")

        digest = hashlib.sha256(audio_file.read_bytes()).hexdigest()[:16]
        remote_file = f"/tmp/alcor-audio-{digest}{audio_file.suffix.lower()}"
        ssh = ["ssh", self.ssh_target]
        subprocess.run(
            [*ssh, "docker", "exec", "-i", self.farm_container, "tee", remote_file],
            input=audio_file.read_bytes(),
            stdout=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            [
                *ssh,
                "docker",
                "exec",
                self.farm_container,
                "env",
                f"PULSE_SERVER={self.pulse_server}",
                "paplay",
                f"--device={self.pulse_sink}",
                remote_file,
            ],
            check=True,
        )
