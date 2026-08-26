import wave

import pytest

from core.audio import AudioInjector


def make_wav(path):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 80)


def test_docker_pulse_copies_and_plays_audio(tmp_path, monkeypatch):
    audio_file = tmp_path / "source.wav"
    make_wav(audio_file)
    calls = []

    def fake_run(command, check):
        calls.append((command, check))

    monkeypatch.setattr("core.audio.subprocess.run", fake_run)
    monkeypatch.setattr("core.audio.time.sleep", lambda _seconds: None)
    injector = AudioInjector(
        project_root=tmp_path,
        mode="docker_pulse",
        docker_command="docker",
        farm_container="alcor-df-emulator-test",
        pulse_server="tcp:127.0.0.1:4713",
        pulse_sink="virtual_mic",
    )

    injector.play(audio_file, wait_after=0)

    assert calls[0][0][:3] == ["docker", "cp", str(audio_file)]
    remote_file = calls[0][0][3].split(":", 1)[1]
    assert calls[1] == (
        [
            "docker", "exec", "alcor-df-emulator-test", "env",
            "PULSE_SERVER=tcp:127.0.0.1:4713", "paplay",
            "--device=virtual_mic", remote_file,
        ],
        True,
    )


def test_docker_pulse_rejects_unsafe_container_name(tmp_path):
    audio_file = tmp_path / "source.wav"
    make_wav(audio_file)
    injector = AudioInjector(
        project_root=tmp_path,
        mode="docker_pulse",
        farm_container="container;rm",
    )

    with pytest.raises(RuntimeError, match="concrete managed container"):
        injector.play(audio_file, wait_after=0)


def test_ssh_docker_pulse_streams_and_plays_audio(tmp_path, monkeypatch):
    audio_file = tmp_path / "source.wav"
    make_wav(audio_file)
    calls = []

    def fake_run(command, check, input=None, stdout=None):
        calls.append((command, check, input, stdout))

    monkeypatch.setattr("core.audio.subprocess.run", fake_run)
    monkeypatch.setattr("core.audio.time.sleep", lambda _seconds: None)
    injector = AudioInjector(
        project_root=tmp_path,
        mode="ssh_docker_pulse",
        farm_container="alcor-df-emulator-test",
        pulse_server="tcp:127.0.0.1:4713",
        pulse_sink="virtual_mic",
        ssh_target="device-farm",
    )

    injector.play(audio_file, wait_after=0)

    remote_file = calls[0][0][-1]
    assert calls[0][0][:6] == ["ssh", "device-farm", "docker", "exec", "-i", "alcor-df-emulator-test"]
    assert calls[0][2] == audio_file.read_bytes()
    assert calls[1][0][-3:] == ["paplay", "--device=virtual_mic", remote_file]


def test_unknown_audio_mode_is_rejected(tmp_path):
    audio_file = tmp_path / "source.wav"
    make_wav(audio_file)
    injector = AudioInjector(project_root=tmp_path, mode="unknown")

    with pytest.raises(ValueError, match="Unsupported audio injection mode"):
        injector.play(audio_file, wait_after=0)


def test_host_prepare_resets_emulator_microphone_bridge(monkeypatch):
    calls = []

    def fake_run(command, check):
        calls.append((command, check))

    monkeypatch.setattr("core.audio.ANDROID_ADB", "adb.exe")
    monkeypatch.setattr("core.audio.ANDROID_ADB_SERIAL", "emulator-5554")
    monkeypatch.setattr("core.audio.subprocess.run", fake_run)
    monkeypatch.setattr("core.audio.time.sleep", lambda _seconds: None)

    AudioInjector(mode="host").prepare()

    assert calls == [
        (["adb.exe", "-s", "emulator-5554", "emu", "avd", "hostmicoff"], True),
        (["adb.exe", "-s", "emulator-5554", "emu", "avd", "hostmicon"], True),
    ]
