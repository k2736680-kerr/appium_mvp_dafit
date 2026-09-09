from types import SimpleNamespace

import pytest

from core import driver_factory
from core.config import APPIUM_SERVER
from core.runtime_isolation import appium_process_is_owned
from scripts import nightly_run


def test_project_uses_dedicated_appium_port_by_default():
    assert APPIUM_SERVER == "http://127.0.0.1:4725/appium-mvp"
    assert nightly_run.DEFAULT_CONFIG["appium_server"] == "http://127.0.0.1:4725/appium-mvp"
    assert nightly_run.DEFAULT_CONFIG["appium_args"][-2:] == ["--base-path", "/appium-mvp"]
    assert "-no-snapshot-load" not in nightly_run.DEFAULT_CONFIG["emulator_extra_args"]


def test_nightly_emulator_requires_snapshot_write_protection(monkeypatch):
    config = {
        **nightly_run.DEFAULT_CONFIG,
        "emulator_avd": "Pixel_8a",
        "emulator_extra_args": ["-gpu", "host"],
    }
    monkeypatch.setattr(nightly_run, "running_emulator_avd", lambda _config: "Pixel_8a")
    monkeypatch.setattr(
        nightly_run,
        "emulator_process_command_lines",
        lambda: [
            "emulator.exe -avd Pixel_8a -allow-host-audio -audio dsound -gpu host"
        ],
    )

    assert "snapshot write protection" in nightly_run.emulator_runtime_mismatch(config)

    monkeypatch.setattr(
        nightly_run,
        "emulator_process_command_lines",
        lambda: [
            "emulator.exe -avd Pixel_8a -allow-host-audio -audio dsound "
            "-gpu host -no-snapshot-save"
        ],
    )
    assert nightly_run.emulator_runtime_mismatch(config) == ""


def test_process_ownership_requires_port_and_project_base_path():
    assert appium_process_is_owned(
        "100", 4725, "node appium --port 4725 --base-path /appium-mvp"
    )
    assert not appium_process_is_owned("100", 4725, "node appium --port 4725")
    assert not appium_process_is_owned(
        "100", 4725, "node appium --port 4723 --base-path /appium-mvp"
    )


def test_driver_recovery_never_restarts_shared_adb(monkeypatch):
    commands = []

    monkeypatch.setattr(driver_factory, "pids_listening_on_port", lambda _port: [])
    monkeypatch.setattr(driver_factory, "owned_appium_pids_on_port", lambda _port: [])
    monkeypatch.setattr(
        driver_factory,
        "_start_detached",
        lambda _command: SimpleNamespace(poll=lambda: None, returncode=None),
    )
    monkeypatch.setattr(driver_factory, "appium_server_is_running", lambda timeout=1: (True, None))
    monkeypatch.setattr(driver_factory.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        driver_factory.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )

    assert driver_factory.restart_appium_server("test isolation") is True
    assert not any("kill-server" in command for command in commands)


def test_driver_recovery_refuses_to_kill_unowned_port_listener(monkeypatch):
    monkeypatch.setattr(driver_factory, "pids_listening_on_port", lambda _port: ["999"])
    monkeypatch.setattr(driver_factory, "owned_appium_pids_on_port", lambda _port: [])
    monkeypatch.setattr(
        driver_factory,
        "_start_detached",
        lambda _command: (_ for _ in ()).throw(AssertionError("must not start over occupied port")),
    )

    assert driver_factory.restart_appium_server("foreign listener") is False


def test_nightly_cleanup_starts_but_never_kills_shared_adb(monkeypatch):
    commands = []
    config = dict(nightly_run.DEFAULT_CONFIG)

    monkeypatch.setattr(nightly_run, "owned_appium_pids_on_port", lambda _port: [])
    monkeypatch.setattr(
        nightly_run,
        "run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(nightly_run.time, "sleep", lambda _seconds: None)

    nightly_run.clean_runtime_before_run(config)

    assert any("start-server" in command for command in commands)
    assert not any("kill-server" in command for command in commands)


def test_nightly_cleanup_stops_only_target_emulator_for_clean_snapshot(monkeypatch):
    commands = []
    config = {
        **nightly_run.DEFAULT_CONFIG,
        "android_adb_serial": "emulator-5554",
        "android_udid": "emulator-5554",
    }

    monkeypatch.setattr(nightly_run, "owned_appium_pids_on_port", lambda _port: [])
    monkeypatch.setattr(nightly_run, "adb_devices", lambda _config: ["emulator-5554", "RFCRC1N229B"])
    monkeypatch.setattr(nightly_run, "wait_for_target_device_absent", lambda _config: True)
    monkeypatch.setattr(
        nightly_run,
        "run",
        lambda command, **_kwargs: commands.append(command)
        or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(nightly_run.time, "sleep", lambda _seconds: None)

    nightly_run.clean_runtime_before_run(config)

    assert [config["adb_exe"], "-s", "emulator-5554", "emu", "kill"] in commands
    assert not any("RFCRC1N229B" in command for command in commands)


def test_nightly_stop_does_not_kill_unowned_listener(monkeypatch):
    commands = []
    config = dict(nightly_run.DEFAULT_CONFIG)

    monkeypatch.setattr(nightly_run, "owned_appium_pids_on_port", lambda _port: [])
    monkeypatch.setattr(
        nightly_run,
        "run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )

    nightly_run.stop_appium(config)

    assert not commands


def test_missing_activity_is_not_a_transient_appium_error():
    error = RuntimeError(
        "Cannot start the app; adbExec failed: Error type 3: Activity class "
        "{com.moyoung.auro.ai/com.moyoung.auro.ai.MainActivity} does not exist"
    )

    assert driver_factory.is_transient_session_error(error) is False
    assert driver_factory.is_transient_session_error(RuntimeError("socket hang up")) is True


def test_emulator_runtime_rejects_same_serial_without_required_host_audio(monkeypatch):
    config = {
        **nightly_run.DEFAULT_CONFIG,
        "emulator_avd": "Pixel_8a",
        "emulator_allow_host_audio": True,
        "emulator_audio_backend": "dsound",
        "emulator_extra_args": ["-no-snapshot-load", "-gpu", "host"],
    }
    monkeypatch.setattr(nightly_run, "running_emulator_avd", lambda _config: "Pixel_8a")
    monkeypatch.setattr(
        nightly_run,
        "emulator_process_command_lines",
        lambda: ["emulator.exe -avd Pixel_8a -audio dsound -gpu host"],
    )

    assert "host microphone" in nightly_run.emulator_runtime_mismatch(config)

    monkeypatch.setattr(
        nightly_run,
        "emulator_process_command_lines",
        lambda: [
            "emulator.exe -avd Pixel_8a -allow-host-audio -audio dsound "
            "-no-snapshot-load -gpu host -no-snapshot-save"
        ],
    )
    assert nightly_run.emulator_runtime_mismatch(config) == ""


def test_target_app_preflight_only_checks_clean_image(monkeypatch):
    commands = []
    monkeypatch.setattr(nightly_run, "target_app_state", lambda _config: (True, "ready"))
    monkeypatch.setattr(nightly_run, "adb_target", lambda *_args, **_kwargs: commands.append(True))

    nightly_run.ensure_target_app(nightly_run.DEFAULT_CONFIG)

    assert commands == []


def test_target_app_preflight_fails_without_modifying_clean_image(monkeypatch):
    monkeypatch.setattr(nightly_run, "target_app_state", lambda _config: (False, "missing"))

    with pytest.raises(RuntimeError, match="不会安装或修改 App"):
        nightly_run.ensure_target_app(nightly_run.DEFAULT_CONFIG)


def test_nightly_restarts_only_configured_emulator_when_runtime_is_wrong(monkeypatch):
    config = {
        **nightly_run.DEFAULT_CONFIG,
        "android_adb_serial": "emulator-5554",
        "emulator_exe": "emulator.exe",
        "emulator_avd": "Pixel_8a",
        "emulator_extra_args": ["-no-snapshot-load", "-gpu", "host"],
    }
    stopped = []
    started = []
    readiness = iter([True, True])

    monkeypatch.setattr(nightly_run, "adb_devices", lambda _config: ["emulator-5554"])
    monkeypatch.setattr(
        nightly_run, "wait_for_device_ready", lambda _config, **_kwargs: next(readiness)
    )
    monkeypatch.setattr(
        nightly_run, "emulator_runtime_mismatch", lambda _config: "host microphone mismatch"
    )
    monkeypatch.setattr(
        nightly_run,
        "stop_emulator",
        lambda target: stopped.append(nightly_run.adb_serial(target)),
    )
    monkeypatch.setattr(nightly_run, "wait_for_target_device_absent", lambda _config: True)
    monkeypatch.setattr(
        nightly_run,
        "run",
        lambda _command, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(nightly_run, "start_detached", lambda command: started.append(command))
    monkeypatch.setattr(nightly_run.Path, "exists", lambda _path: True)

    nightly_run.ensure_emulator(config)

    assert stopped == ["emulator-5554"]
    assert started == [[
        "emulator.exe", "-avd", "Pixel_8a", "-allow-host-audio", "-audio", "dsound",
        "-no-snapshot-load", "-gpu", "host", "-no-snapshot-save",
    ]]
