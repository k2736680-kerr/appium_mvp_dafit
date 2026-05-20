import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

from appium import webdriver
from appium.options.android import UiAutomator2Options

from core.config import (
    ANDROID_ADB,
    ANDROID_ALLOW_HOST_AUDIO,
    ANDROID_AUDIO_BACKEND,
    ANDROID_AUTO_START_EMULATOR,
    ANDROID_AVD,
    ANDROID_BOOT_TIMEOUT_SECONDS,
    ANDROID_EMULATOR,
    ANDROID_UDID,
    APPIUM_SERVER,
    APP_ACTIVITY,
    APP_PACKAGE,
    LAUNCHER_ACTIVITY,
    LAUNCHER_PACKAGE,
    PROJECT_ROOT,
)


TRANSIENT_SESSION_ERRORS = (
    "connectionreseterror",
    "connection aborted",
    "protocolerror",
    "cannot start the",
    "error getting device api level",
    "adbexec",
    "socket hang up",
    "failed to establish a new connection",
    "max retries exceeded",
    "connection refused",
    "cannot be proxied to uiautomator2 server",
    "instrumentation process is not running",
)


def _adb_command():
    if ANDROID_ADB and shutil.which(ANDROID_ADB):
        return ANDROID_ADB
    if ANDROID_ADB and "\\" in ANDROID_ADB and shutil.which("adb"):
        return "adb"
    return ANDROID_ADB or "adb"


def _run_adb(args, timeout=15):
    return subprocess.run(
        [_adb_command(), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _connected_adb_devices():
    result = _run_adb(["devices"], timeout=10)
    output = result.stdout or ""
    devices = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices, output


def _target_device(devices):
    if ANDROID_UDID and ANDROID_UDID in devices:
        return ANDROID_UDID
    if not ANDROID_UDID and devices:
        return devices[0]
    return None


def android_device_shell_ready(device, timeout=10):
    try:
        boot = _run_adb(["-s", device, "shell", "getprop", "sys.boot_completed"], timeout=timeout)
        anim = _run_adb(["-s", device, "shell", "getprop", "init.svc.bootanim"], timeout=timeout)
        ping = _run_adb(["-s", device, "shell", "echo", "ready"], timeout=timeout)
    except Exception:
        return False

    return (
        boot.returncode == 0
        and (boot.stdout or "").strip() == "1"
        and anim.returncode == 0
        and (anim.stdout or "").strip() in {"stopped", ""}
        and ping.returncode == 0
        and "ready" in (ping.stdout or "")
    )


def wait_for_android_device_ready(timeout_seconds=None):
    timeout_seconds = int(timeout_seconds or ANDROID_BOOT_TIMEOUT_SECONDS)
    deadline = time.time() + timeout_seconds
    last_state = "not checked"

    while time.time() < deadline:
        try:
            devices, _ = _connected_adb_devices()
        except Exception as exc:
            devices = []
            last_state = f"adb devices failed: {exc}"
        device = _target_device(devices)
        if device:
            if android_device_shell_ready(device, timeout=10):
                return True
            last_state = f"device listed but shell not ready: {device}"
        else:
            last_state = f"waiting for adb device; devices={devices}"
        time.sleep(5)

    print(f"[RECOVERY] Android device not ready before timeout: {last_state}")
    return False


def _start_emulator_detached():
    if not ANDROID_EMULATOR or not ANDROID_AVD or not os.path.exists(ANDROID_EMULATOR):
        return False

    command = [ANDROID_EMULATOR, "-avd", ANDROID_AVD]
    if ANDROID_ALLOW_HOST_AUDIO:
        command.append("-allow-host-audio")
    if ANDROID_AUDIO_BACKEND:
        command.extend(["-audio", ANDROID_AUDIO_BACKEND])
    print(f"[RECOVERY] Starting Android emulator: {ANDROID_AVD}")
    _start_detached(command, hidden=False)
    return True


def _kill_emulator_processes():
    if os.name != "nt":
        return
    subprocess.run(["taskkill", "/IM", "emulator.exe", "/F"], capture_output=True, text=True, timeout=20, check=False)
    subprocess.run(["taskkill", "/IM", "qemu-system-x86_64.exe", "/F"], capture_output=True, text=True, timeout=20, check=False)


def ensure_android_device_ready(restart_if_unresponsive=False):
    try:
        devices, output = _connected_adb_devices()
    except Exception as exc:
        raise RuntimeError(
            "Android device preflight failed: cannot execute adb devices.\n"
            f"adb path: {_adb_command()}\n"
            f"Original error: {exc}"
        )

    device = _target_device(devices)
    if device and android_device_shell_ready(device):
        return

    if device and not restart_if_unresponsive:
        if wait_for_android_device_ready(timeout_seconds=60):
            return

    if not ANDROID_AUTO_START_EMULATOR:
        if device:
            raise RuntimeError(
                f"Android device is listed by adb but not ready: {device}\n"
                "Set ANDROID_AUTO_START_EMULATOR=1 to allow automatic emulator recovery."
            )
        raise RuntimeError(
            "No usable Android device found.\n"
            f"Expected device: {ANDROID_UDID}\n"
            f"adb output:\n{output.strip() or '(empty)'}"
        )

    if device:
        print(f"[RECOVERY] Android device is listed but unresponsive: {device}; restarting emulator")
        _kill_emulator_processes()
        subprocess.run([_adb_command(), "kill-server"], capture_output=True, text=True, timeout=15, check=False)
        subprocess.run([_adb_command(), "start-server"], capture_output=True, text=True, timeout=15, check=False)

    if not device:
        subprocess.run([_adb_command(), "start-server"], capture_output=True, text=True, timeout=15, check=False)
        try:
            refreshed_devices, _ = _connected_adb_devices()
        except Exception:
            refreshed_devices = []
        device = _target_device(refreshed_devices)
        if device and wait_for_android_device_ready(timeout_seconds=60):
            return

        # A stale emulator process can hold the AVD lock while adb reports no devices.
        # Clear it before launching the configured AVD so the boot attempt is not doomed.
        _kill_emulator_processes()
        subprocess.run([_adb_command(), "kill-server"], capture_output=True, text=True, timeout=15, check=False)
        subprocess.run([_adb_command(), "start-server"], capture_output=True, text=True, timeout=15, check=False)

    if not _start_emulator_detached() and not wait_for_android_device_ready(timeout_seconds=30):
        raise RuntimeError(
            "No usable Android device found, and the configured emulator could not be started.\n"
            f"Expected device: {ANDROID_UDID}\n"
            f"emulator: {ANDROID_EMULATOR}\n"
            f"avd: {ANDROID_AVD}\n"
            f"adb output:\n{output.strip() or '(empty)'}"
        )

    if not wait_for_android_device_ready():
        raise RuntimeError(
            f"Android emulator did not become ready within {ANDROID_BOOT_TIMEOUT_SECONDS} seconds.\n"
            f"Expected device: {ANDROID_UDID}"
        )


def appium_server_is_running(timeout=3):
    status_url = APPIUM_SERVER.rstrip("/") + "/status"
    try:
        with urllib.request.urlopen(status_url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return False, exc

    if not isinstance(data, dict):
        return False, RuntimeError(f"Unexpected Appium status response: {data}")
    return True, None


def assert_appium_server_running():
    ok, exc = appium_server_is_running()
    if ok:
        return

    if restart_appium_server("Appium server not reachable before creating a session"):
        ok, exc = appium_server_is_running()
        if ok:
            return

    raise RuntimeError(
        "Appium Server 未启动或无法连接。\n"
        f"当前地址：{APPIUM_SERVER}\n"
        "请先另开一个 CMD 窗口执行：\n"
        "appium --address 0.0.0.0 --port 4723\n"
        f"原始错误：{exc}"
    )


def assert_android_device_connected():
    ensure_android_device_ready()


def assert_runtime_ready():
    assert_appium_server_running()
    assert_android_device_connected()


def _appium_port():
    try:
        return int(APPIUM_SERVER.rstrip("/").rsplit(":", 1)[1])
    except Exception:
        return 4723


def _pids_listening_on_port(port):
    if os.name != "nt":
        return []

    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return []

    pids = set()
    marker = f":{port}"
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[1].endswith(marker) and parts[3].upper() == "LISTENING" and parts[-1].isdigit():
            pids.add(parts[-1])
    return sorted(pids)


def _start_detached(command, hidden=True):
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    log_dir = PROJECT_ROOT / "reports"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout = open(log_dir / "appium_server_stdout.log", "ab")
        stderr = open(log_dir / "appium_server_stderr.log", "ab")
    except OSError:
        stdout = subprocess.DEVNULL
        stderr = subprocess.DEVNULL

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        if hidden:
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return process
    finally:
        if stdout is not subprocess.DEVNULL:
            stdout.close()
        if stderr is not subprocess.DEVNULL:
            stderr.close()


def _appium_start_command():
    command = os.environ.get("APPIUM_COMMAND")
    if not command:
        command = "appium.cmd" if os.name == "nt" else "appium"

    args_value = os.environ.get("APPIUM_ARGS_JSON")
    if args_value:
        try:
            args = json.loads(args_value)
            if isinstance(args, list):
                return [command, *[str(item) for item in args]]
        except json.JSONDecodeError:
            pass

    return [command, "--address", "0.0.0.0", "--port", str(_appium_port())]


def restart_appium_server(reason=""):
    if os.environ.get("APPIUM_AUTO_RESTART", "1").lower() in {"0", "false", "no"}:
        print(f"[RECOVERY] Appium auto restart disabled. reason={reason}")
        return False

    port = _appium_port()
    print(f"[RECOVERY] Restarting Appium on port {port}. reason={reason}")

    for pid in _pids_listening_on_port(port):
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, text=True, check=False)

    adb = _adb_command()
    subprocess.run([adb, "kill-server"], capture_output=True, text=True, timeout=15, check=False)
    subprocess.run([adb, "start-server"], capture_output=True, text=True, timeout=15, check=False)

    process = _start_detached(_appium_start_command())
    ready_count = 0
    for _ in range(60):
        if process.poll() is not None:
            print(f"[RECOVERY] Appium exited while starting with code {process.returncode}")
            return False

        ok, _ = appium_server_is_running(timeout=1)
        if ok:
            ready_count += 1
            if ready_count >= 3:
                print("[RECOVERY] Appium is ready again")
                return True
        else:
            ready_count = 0
        time.sleep(1)

    print("[RECOVERY] Appium did not become ready after restart")
    return False


def is_transient_session_error(exc):
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in TRANSIENT_SESSION_ERRORS)


def _new_remote_driver(caps):
    options = UiAutomator2Options().load_capabilities(caps)
    return webdriver.Remote(APPIUM_SERVER, options=options)


def quit_driver_safely(driver, timeout=45):
    if driver is None:
        return

    result = {"error": None}

    def do_quit():
        try:
            driver.quit()
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=do_quit, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        print(f"[RECOVERY] driver.quit timed out after {timeout}s")
        restart_appium_server("driver.quit timeout")
        thread.join(5)
        return

    if result["error"] is not None:
        print(f"[RECOVERY] driver.quit raised: {result['error']}")
        if is_transient_session_error(result["error"]):
            restart_appium_server("driver.quit transient error")


def create_driver(start_mode="app", no_reset=True):
    """
    start_mode:
      - app: directly start the target app
      - launcher: start the launcher for recordings that begin from the icon
    """
    assert_runtime_ready()

    if start_mode == "launcher":
        app_package = LAUNCHER_PACKAGE
        app_activity = LAUNCHER_ACTIVITY
        app_wait_activity = "*"
    else:
        app_package = APP_PACKAGE
        app_activity = APP_ACTIVITY
        app_wait_activity = "*"

    caps = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:deviceName": ANDROID_UDID,
        "appium:udid": ANDROID_UDID,
        "appium:appPackage": app_package,
        "appium:appActivity": app_activity,
        "appium:appWaitActivity": app_wait_activity,
        "appium:noReset": no_reset,
        "appium:autoGrantPermissions": True,
        "appium:adbExecTimeout": 60000,
        "appium:uiautomator2ServerInstallTimeout": 60000,
        "appium:uiautomator2ServerLaunchTimeout": 60000,
        "appium:newCommandTimeout": 300,
    }

    try:
        return _new_remote_driver(caps)
    except Exception as exc:
        if not is_transient_session_error(exc):
            raise

        print(f"[RECOVERY] create_driver failed with transient Appium error: {exc}")
        restart_appium_server("create_driver transient error")
        assert_runtime_ready()
        return _new_remote_driver(caps)
