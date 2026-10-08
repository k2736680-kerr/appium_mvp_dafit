import argparse
import atexit
import base64
import contextlib
import datetime as dt
import hashlib
import hmac
import html
import json
import locale
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_isolation import owned_appium_pids_on_port, pids_listening_on_port


REPORT_ROOT = PROJECT_ROOT / "reports"
CONFIG_PATH = PROJECT_ROOT / "scripts" / "nightly_config.local.json"

DEFAULT_CONFIG = {
    "suite_name": "Auro AI 自动化回归",
    "dingtalk_keyword": "test",
    "dingtalk_webhook": "",
    "dingtalk_secret": "",
    "send_window_start": "23:30",
    "send_window_end": "05:00",
    "report_server_host": "0.0.0.0",
    "report_server_port": 8876,
    "report_base_url": "",
    "start_report_server_if_missing": True,
    "start_appium_if_missing": True,
    "clean_runtime_before_run": True,
    "stop_appium_after_run": True,
    "appium_command": "appium.cmd",
    "appium_args": ["--address", "0.0.0.0", "--port", "4725", "--base-path", "/appium-mvp"],
    "appium_server": "http://127.0.0.1:4725/appium-mvp",
    "appium_udid": "emulator-5554",
    "appium_auto_restart": True,
    "app_package": "com.moyoung.auro.ai",
    "app_activity": ".MainActivity",
    "start_emulator_if_missing": True,
    "stop_emulator_after_run": True,
    "emulator_exe": r"E:\android_sdk\emulator\emulator.exe",
    "emulator_avd": "Pixel_8a",
    "emulator_allow_host_audio": True,
    "emulator_audio_backend": "dsound",
    "emulator_extra_args": [],
    "auro_auto_login": True,
    "auro_login_account": "",
    "auro_login_password": "",
    "adb_exe": r"E:\android_sdk\platform-tools\adb.exe",
    "android_udid": "emulator-5554",
    "android_adb_serial": "",
    "audio_injection_mode": "host",
    "device_farm_docker_command": "docker",
    "device_farm_ssh_target": "",
    "device_farm_emulator_container": "",
    "device_farm_pulse_server": "tcp:127.0.0.1:4713",
    "device_farm_pulse_sink": "virtual_mic",
    "emulator_boot_timeout_seconds": 240,
    "emulator_post_boot_wait_seconds": 25,
    "pytest_timeout_seconds": 14400,
    "qiniu_access_key": "",
    "qiniu_secret_key": "",
    "qiniu_bucket": "",
    "qiniu_domain": "",
    "qiniu_region": "z0",
    "qiniu_upload_url": "",
    "qiniu_prefix": "appium-reports",
    "qiniu_upload_scope": "failed_only",
    "qiniu_upload_xml": False,
    "qiniu_upload_timeout_seconds": 60,
    "qiniu_upload_retries": 2,
    "qiniu_upload_retry_backoff_seconds": 3,
    "qiniu_fallback_to_local_report": True,
}

SUBPROCESS_ENCODING = locale.getencoding()


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return config


def now_local():
    return dt.datetime.now()


def parse_hhmm(value):
    hour, minute = value.split(":", 1)
    return dt.time(int(hour), int(minute))


def within_send_window(config, current=None):
    current = current or now_local()
    start = parse_hhmm(config["send_window_start"])
    end = parse_hhmm(config["send_window_end"])
    current_time = current.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= current_time <= end
    return current_time >= start or current_time <= end


def run(command, timeout=None, env=None, text_encoding=None):
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        encoding=text_encoding or SUBPROCESS_ENCODING,
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def url_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def _is_rfc2544_benchmark_ip(ip: str) -> bool:
    """198.18.0.0/15 is benchmark space; VPN/加速器常把出口伪装成这里，不能当局域网报告地址。"""
    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        first, second = int(parts[0]), int(parts[1])
        return first == 198 and 18 <= second <= 19
    except ValueError:
        return False


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidate = sock.getsockname()[0]
            if candidate and not _is_rfc2544_benchmark_ip(candidate):
                return candidate
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def build_report_base_url(config):
    if config.get("report_base_url"):
        return config["report_base_url"].rstrip("/")
    return f"http://{local_ip()}:{int(config['report_server_port'])}"


def start_detached(command):
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    executable = str(command[0]).lower() if command else ""
    is_emulator = executable.endswith(("emulator", "emulator.exe"))
    if command and str(command[0]).lower().endswith(("appium", "appium.cmd")):
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        stdout = open(REPORT_ROOT / "appium_server_stdout.log", "ab")
        stderr = open(REPORT_ROOT / "appium_server_stderr.log", "ab")
    elif is_emulator:
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        stdout = open(REPORT_ROOT / "emulator_stdout.log", "ab")
        stderr = open(REPORT_ROOT / "emulator_stderr.log", "ab")

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        if not is_emulator:
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    finally:
        if stdout is not subprocess.DEVNULL:
            stdout.close()
        if stderr is not subprocess.DEVNULL:
            stderr.close()


def ensure_report_server(config):
    base_url = build_report_base_url(config)
    local_url = f"http://127.0.0.1:{int(config['report_server_port'])}/"
    if url_ok(local_url):
        return base_url

    if not config.get("start_report_server_if_missing", True):
        return base_url

    python_exe = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)
    start_detached([
        str(python_exe),
        str(PROJECT_ROOT / "scripts" / "report_server.py"),
        "--host",
        str(config["report_server_host"]),
        "--port",
        str(config["report_server_port"]),
    ])

    for _ in range(20):
        if url_ok(local_url, timeout=1):
            break
        time.sleep(0.5)
    return base_url


def appium_status_url():
    return os.environ.get("APPIUM_SERVER", "http://127.0.0.1:4725/appium-mvp").rstrip("/") + "/status"


def ensure_appium(config):
    if url_ok(appium_status_url()):
        return
    if not config.get("start_appium_if_missing", True):
        return
    port = appium_port_from_config(config)
    listeners = pids_listening_on_port(port)
    if listeners:
        raise RuntimeError(
            f"Appium 专用端口 {port} 已被非本项目服务占用（PID: {', '.join(listeners)}）；"
            "为避免影响其他项目，本项目不会终止该进程。"
        )
    start_detached([config["appium_command"], *config.get("appium_args", [])])
    for _ in range(60):
        if url_ok(appium_status_url(), timeout=1):
            return
        time.sleep(1)
    raise RuntimeError(
        "Appium Server 启动失败或 60 秒内未就绪。\n"
        f"启动命令：{config['appium_command']} {' '.join(config.get('appium_args', []))}\n"
        f"状态地址：{appium_status_url()}\n"
        "请检查 reports/appium_server_stdout.log 和 reports/appium_server_stderr.log。"
    )


def adb_devices(config):
    adb = config.get("adb_exe") or "adb"
    result = run([adb, "devices"], timeout=15)
    devices = []
    for line in (getattr(result, "stdout", "") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def adb_serial(config):
    return config.get("android_adb_serial") or config.get("android_udid")


def adb_shell(config, args, timeout=10):
    adb = config.get("adb_exe") or "adb"
    udid = adb_serial(config)
    command = [adb]
    if udid:
        command.extend(["-s", udid])
    command.extend(["shell", *args])
    return run(command, timeout=timeout)


def adb_target(config, args, timeout=10):
    adb = config.get("adb_exe") or "adb"
    udid = adb_serial(config)
    command = [adb]
    if udid:
        command.extend(["-s", udid])
    command.extend(args)
    return run(command, timeout=timeout)


def running_emulator_avd(config):
    """Return the AVD name behind the explicitly configured emulator serial."""
    udid = str(adb_serial(config) or "")
    if not udid.startswith("emulator-"):
        return ""
    result = adb_target(config, ["emu", "avd", "name"], timeout=10)
    if result.returncode != 0:
        return ""
    return next(
        (line.strip() for line in (result.stdout or "").splitlines() if line.strip() and line.strip() != "OK"),
        "",
    )


def emulator_process_command_lines():
    """Read emulator command lines without changing any process state."""
    if os.name != "nt":
        return []
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -in @('emulator.exe','qemu-system-x86_64.exe',"
        "'qemu-system-i386.exe','qemu-system-aarch64.exe') } | "
        "ForEach-Object { $_.CommandLine }"
    )
    result = run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=15,
    )
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def emulator_runtime_mismatch(config):
    """Explain why an online emulator is unsafe to reuse, or return an empty string."""
    udid = str(adb_serial(config) or "")
    if not udid.startswith("emulator-"):
        return ""

    expected_avd = str(config.get("emulator_avd") or "").strip()
    actual_avd = running_emulator_avd(config)
    if expected_avd and actual_avd != expected_avd:
        return f"AVD mismatch: expected={expected_avd!r}, actual={actual_avd!r}"

    if os.name != "nt":
        return ""

    normalized_avd = expected_avd.lower()
    candidates = []
    for command_line in emulator_process_command_lines():
        normalized = " ".join(command_line.lower().replace('"', "").split())
        if normalized_avd and (
            f"-avd {normalized_avd}" in normalized or f"-avd={normalized_avd}" in normalized
        ):
            candidates.append(normalized)
    if not candidates:
        return f"cannot verify the owner/launch command of AVD {expected_avd!r}"

    # Nightly runs deliberately mutate app/system state.  Loading the curated
    # baseline snapshot is desired, but Quick Boot must never overwrite that
    # baseline when the emulator exits.
    required_markers = [("snapshot write protection", ("-no-snapshot-save",))]
    if config.get("emulator_allow_host_audio", True):
        required_markers.append(("host microphone", ("-allow-host-audio",)))
    audio_backend = str(config.get("emulator_audio_backend") or "").strip().lower()
    if audio_backend:
        required_markers.append(
            ("audio backend", (f"-audio {audio_backend}", f"-audio={audio_backend}"))
        )
    for index, item in enumerate([str(arg) for arg in config.get("emulator_extra_args", [])]):
        if item == "-gpu" and index + 1 < len(config.get("emulator_extra_args", [])):
            gpu = str(config["emulator_extra_args"][index + 1]).lower()
            required_markers.append(("GPU backend", (f"-gpu {gpu}", f"-gpu={gpu}")))

    for command in candidates:
        if all(any(marker in command for marker in markers) for _label, markers in required_markers):
            return ""
    missing = [
        label
        for label, markers in required_markers
        if not any(any(marker in command for marker in markers) for command in candidates)
    ]
    return f"emulator launch mismatch for AVD {expected_avd!r}; missing={missing or 'combined ownership match'}"


def target_app_state(config):
    package_name = str(config.get("app_package") or "com.moyoung.auro.ai")
    activity_name = str(config.get("app_activity") or ".MainActivity")
    package = adb_shell(config, ["pm", "path", package_name], timeout=20)
    activity = adb_shell(
        config,
        [
            "cmd", "package", "resolve-activity", "--brief",
            "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER",
            package_name,
        ],
        timeout=20,
    )
    resolved = (activity.stdout or "").strip()
    full_activity = activity_name if activity_name.startswith(package_name) else f"{package_name}{activity_name}"
    components = {f"{package_name}/{activity_name}", f"{package_name}/{full_activity}"}
    activity_found = full_activity in resolved or any(component in resolved for component in components)
    available = (
        package.returncode == 0
        and bool((package.stdout or "").strip())
        and activity.returncode == 0
        and activity_found
    )
    detail = (
        f"package_rc={package.returncode}, activity_rc={activity.returncode}, resolved={resolved!r}"
    )
    return available, detail


def ensure_target_app(config):
    available, detail = target_app_state(config)
    if not available:
        raise RuntimeError(
            "Auro App 预检失败：干净模拟器镜像里没有配置的 Auro 包或启动页。"
            f"设备={adb_serial(config)!r}; {detail}。请修复干净镜像后再运行，夜跑不会安装或修改 App。"
        )
    print(f"Auro App preflight passed on clean image: {adb_serial(config)}")


def device_shell_ready(config, timeout=10):
    try:
        boot = adb_shell(config, ["getprop", "sys.boot_completed"], timeout=timeout)
        anim = adb_shell(config, ["getprop", "init.svc.bootanim"], timeout=timeout)
        ping = adb_shell(config, ["echo", "ready"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False

    return (
        boot.returncode == 0
        and (boot.stdout or "").strip() == "1"
        and anim.returncode == 0
        and (anim.stdout or "").strip() in {"stopped", ""}
        and ping.returncode == 0
        and "ready" in (ping.stdout or "")
    )


def wait_for_device_ready(config, timeout_seconds=None):
    timeout_seconds = int(timeout_seconds or config.get("emulator_boot_timeout_seconds", 240))
    post_boot_wait_seconds = float(config.get("emulator_post_boot_wait_seconds", 0) or 0)
    udid = adb_serial(config)
    deadline = time.time() + timeout_seconds
    last_state = "not checked"

    while time.time() < deadline:
        devices = adb_devices(config)
        if (udid and udid in devices) or (not udid and devices):
            if device_shell_ready(config, timeout=10):
                if post_boot_wait_seconds > 0:
                    print(
                        "Android emulator shell is ready; "
                        f"waiting {post_boot_wait_seconds:g}s for launcher/system settle."
                    )
                    time.sleep(post_boot_wait_seconds)
                return True
            last_state = "adb device listed, shell not ready"
        else:
            last_state = f"waiting for adb device; devices={devices}"
        time.sleep(5)

    print(f"Android emulator was not ready before timeout: {last_state}")
    return False


def appium_port_from_config(config):
    args = [str(item) for item in config.get("appium_args", [])]
    for index, item in enumerate(args):
        if item == "--port" and index + 1 < len(args):
            try:
                return int(args[index + 1])
            except ValueError:
                pass

    status_url = appium_status_url()
    try:
        return int(status_url.rstrip("/").rsplit(":", 1)[1].split("/", 1)[0])
    except Exception:
        return 4725


def stop_appium(config):
    if not config.get("stop_appium_after_run", True):
        return

    for pid in owned_appium_pids_on_port(appium_port_from_config(config)):
        result = run(["taskkill", "/PID", pid, "/T", "/F"], timeout=20)
        if result.returncode == 0:
            print(f"Stopped Appium process tree on port {appium_port_from_config(config)}: pid={pid}")


def clean_runtime_before_run(config):
    if not config.get("clean_runtime_before_run", True):
        return

    print("Cleaning stale Appium/emulator runtime before nightly run.")
    for pid in owned_appium_pids_on_port(appium_port_from_config(config)):
        result = run(["taskkill", "/PID", pid, "/T", "/F"], timeout=20)
        if result.returncode == 0:
            print(f"Stopped stale Appium process tree: pid={pid}")
    # Always boot this run from the configured clean AVD snapshot. Only the
    # configured target serial may be stopped; the shared ADB daemon stays up.
    udid = adb_serial(config)
    if udid and udid in adb_devices(config):
        print(f"Stopping target emulator {udid} to load the clean snapshot.")
        run([config.get("adb_exe") or "adb", "-s", udid, "emu", "kill"], timeout=20)
        if not wait_for_target_device_absent(config):
            raise RuntimeError(f"Configured emulator {udid} did not stop; refusing to reuse a dirty runtime.")
    adb = config.get("adb_exe") or "adb"
    run([adb, "start-server"], timeout=15)
    time.sleep(2)


def stop_emulator(config):
    if not config.get("stop_emulator_after_run", True):
        return

    adb = config.get("adb_exe") or "adb"
    udid = adb_serial(config)
    if udid and udid in adb_devices(config):
        run([adb, "-s", udid, "emu", "kill"], timeout=20)
        time.sleep(2)


def wait_for_target_device_absent(config, timeout_seconds=30):
    udid = adb_serial(config)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not udid or udid not in adb_devices(config):
            return True
        time.sleep(1)
    return False

def cleanup_runtime_services(config):
    # Leave the report server running so DingTalk links remain reachable.
    stop_appium(config)
    stop_emulator(config)


_CLEANUP_REGISTERED = False


def register_runtime_cleanup(config):
    global _CLEANUP_REGISTERED
    if _CLEANUP_REGISTERED:
        return
    if not config.get("stop_appium_after_run", True) and not config.get("stop_emulator_after_run", True):
        return

    atexit.register(cleanup_runtime_services, dict(config))
    _CLEANUP_REGISTERED = True


def ensure_emulator(config):
    udid = adb_serial(config)
    devices = adb_devices(config)
    if (udid and udid in devices) or (not udid and devices):
        if wait_for_device_ready(config, timeout_seconds=60):
            mismatch = emulator_runtime_mismatch(config)
            if not mismatch:
                return
            print(
                f"Configured Android target {udid} is online but unsafe to reuse: {mismatch}. "
                "Restarting only that configured emulator serial."
            )
            stop_emulator({**config, "stop_emulator_after_run": True})
            if not wait_for_target_device_absent(config):
                raise RuntimeError(
                    f"Configured emulator {udid} did not stop; refusing to start a second instance."
                )
            devices = []
        if not config.get("start_emulator_if_missing", True):
            return
        if udid in devices:
            print("Existing Android emulator is listed but not responsive; restarting it.")
            stop_emulator({**config, "stop_emulator_after_run": True})
            run([config.get("adb_exe") or "adb", "start-server"], timeout=15)

    if not config.get("start_emulator_if_missing", True):
        return

    emulator = config.get("emulator_exe")
    avd = config.get("emulator_avd")
    if not emulator or not avd or not Path(emulator).exists():
        return

    run([config.get("adb_exe") or "adb", "start-server"], timeout=15)

    command = [emulator, "-avd", avd]
    if config.get("emulator_allow_host_audio", True):
        command.append("-allow-host-audio")
    audio_backend = str(config.get("emulator_audio_backend") or "").strip()
    if audio_backend:
        command.extend(["-audio", audio_backend])
    command.extend(str(arg) for arg in config.get("emulator_extra_args", []))
    if "-no-snapshot-save" not in command:
        command.append("-no-snapshot-save")

    start_detached(command)
    if wait_for_device_ready(config):
        return

    print("Android emulator did not become ready after first launch; retrying without snapshot.")
    stop_emulator({**config, "stop_emulator_after_run": True})
    run([config.get("adb_exe") or "adb", "start-server"], timeout=15)

    retry_command = list(command)
    if "-no-snapshot-load" not in retry_command:
        retry_command.append("-no-snapshot-load")
    start_detached(retry_command)
    if not wait_for_device_ready(config):
        raise RuntimeError(
            f"Android emulator did not become ready within "
            f"{config.get('emulator_boot_timeout_seconds', 240)} seconds"
        )


def latest_report_bundle():
    bundles = sorted(
        REPORT_ROOT.glob("recording_report_bundle_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return bundles[0] if bundles else None


def read_manifest(bundle_dir):
    manifest_path = bundle_dir / "archive_manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def report_link(bundle_dir, base_url):
    report_path = bundle_dir / "recording_report.html"
    if not report_path.exists():
        report_path = next(bundle_dir.glob("*.html"), report_path)
    relative = report_path.relative_to(REPORT_ROOT).as_posix()
    return f"{base_url}/{urllib.parse.quote(relative)}"


def _safe_case_id(name: str) -> str:
    result = []
    for ch in name:
        if ch.isalnum() or ch in "_-":
            result.append(ch)
        else:
            result.append("_")
    return "".join(result).strip("_") or "recording_case"


def failed_cases_from_report(bundle_dir, limit=8):
    report_path = bundle_dir / "recording_report.html"
    if not report_path.exists():
        return []

    text = report_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r'<div class="test-case[^"]*" data-status="(FAILED|ERROR)".*?'
        r'<div class="case-title">(.*?)</div>',
        re.S,
    )
    cases = []
    for match in pattern.finditer(text):
        status = html.unescape(match.group(1)).strip()
        name = html.unescape(match.group(2)).strip()
        name = re.sub(r"\s+", " ", name)
        cases.append({"status": status, "name": name})
        if limit is not None and len(cases) >= limit:
            break
    return cases


def qiniu_requested(config):
    keys = (
        "qiniu_access_key",
        "qiniu_secret_key",
        "qiniu_bucket",
        "qiniu_domain",
        "qiniu_upload_url",
    )
    return bool(config.get("qiniu_enabled")) or any(str(config.get(key) or "").strip() for key in keys)


def normalize_qiniu_region(region):
    value = str(region or "z0").strip()
    aliases = {
        "华东": "z0",
        "华东-浙江": "z0",
        "华东浙江": "z0",
        "华北": "z1",
        "华南": "z2",
        "北美": "na0",
        "东南亚": "as0",
    }
    return aliases.get(value, value)


def qiniu_upload_url(config):
    explicit = str(config.get("qiniu_upload_url") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    region = normalize_qiniu_region(config.get("qiniu_region"))
    endpoints = {
        "z0": "https://upload-z0.qiniup.com",
        "z1": "https://upload-z1.qiniup.com",
        "z2": "https://upload-z2.qiniup.com",
        "na0": "https://upload-na0.qiniup.com",
        "as0": "https://upload-as0.qiniup.com",
    }
    if region not in endpoints:
        raise RuntimeError(
            f"未知七牛区域 {config.get('qiniu_region')!r}，请配置 qiniu_upload_url 明确上传入口。"
        )
    return endpoints[region]


def normalize_qiniu_prefix(prefix):
    parts = [item for item in str(prefix or "").replace("\\", "/").split("/") if item]
    return "/".join(parts)


def validate_qiniu_config(config):
    required = ("qiniu_access_key", "qiniu_secret_key", "qiniu_bucket", "qiniu_domain")
    missing = [key for key in required if not str(config.get(key) or "").strip()]
    if missing:
        raise RuntimeError(f"七牛上传配置缺失：{', '.join(missing)}")

    bucket = str(config["qiniu_bucket"]).strip()
    if "/" in bucket:
        raise RuntimeError(
            "qiniu_bucket 是七牛存储空间名称，不是目录路径；"
            "如果要上传到根目录下的 auroai/，请把 qiniu_prefix 配成 auroai/appium-reports。"
        )


def qiniu_urlsafe_base64(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii")


def qiniu_upload_token(config, key):
    deadline = int(time.time()) + 3600
    policy = {
        "scope": f"{config['qiniu_bucket']}:{key}",
        "deadline": deadline,
    }
    policy_json = json.dumps(policy, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded_policy = qiniu_urlsafe_base64(policy_json)
    digest = hmac.new(
        str(config["qiniu_secret_key"]).encode("utf-8"),
        encoded_policy.encode("ascii"),
        hashlib.sha1,
    ).digest()
    encoded_sign = qiniu_urlsafe_base64(digest)
    return f"{config['qiniu_access_key']}:{encoded_sign}:{encoded_policy}"


def multipart_form_data(fields, file_field, file_path):
    boundary = f"----appium-mvp-{int(time.time() * 1000)}-{os.getpid()}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def qiniu_upload_file(config, file_path, key):
    token = qiniu_upload_token(config, key)
    body, content_type = multipart_form_data(
        {"token": token, "key": key},
        "file",
        file_path,
    )
    upload_url = qiniu_upload_url(config)
    timeout = int(config.get("qiniu_upload_timeout_seconds", 60))
    retries = max(0, int(config.get("qiniu_upload_retries", 2)))
    attempts = retries + 1
    backoff_seconds = float(config.get("qiniu_upload_retry_backoff_seconds", 3))

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            upload_url,
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result_body = response.read().decode("utf-8", errors="replace")
                if response.status >= 400:
                    raise RuntimeError(f"七牛上传失败 HTTP {response.status}: {result_body}")
                return json.loads(result_body) if result_body else {}
        except Exception as exc:
            if attempt >= attempts:
                raise
            print(
                "Qiniu upload failed; retrying "
                f"({attempt}/{attempts}) key={key}: {type(exc).__name__}: {exc}"
            )
            if backoff_seconds > 0:
                time.sleep(backoff_seconds)

    raise RuntimeError(f"七牛上传失败且未返回结果: {key}")


def copy_qiniu_publish_bundle(bundle_dir, config, failed_cases):
    publish_dir = REPORT_ROOT / "qiniu_publish" / bundle_dir.name
    if publish_dir.exists():
        shutil.rmtree(publish_dir)
    publish_dir.mkdir(parents=True, exist_ok=True)

    for name in ("recording_report.html", "archive_manifest.json"):
        source = bundle_dir / name
        if source.exists():
            shutil.copy2(source, publish_dir / name)

    scope = str(config.get("qiniu_upload_scope") or "failed_only").lower()
    upload_xml = bool(config.get("qiniu_upload_xml", False))
    artifacts_dir = bundle_dir / "artifacts"
    publish_artifacts_dir = publish_dir / "artifacts"

    if scope == "all":
        if artifacts_dir.exists():
            ignore = None if upload_xml else shutil.ignore_patterns("*.xml")
            shutil.copytree(artifacts_dir, publish_artifacts_dir, ignore=ignore)
    elif scope == "failed_only":
        for item in failed_cases:
            case_dir = artifacts_dir / _safe_case_id(item["name"])
            if not case_dir.exists():
                continue
            target_dir = publish_artifacts_dir / case_dir.name
            target_dir.mkdir(parents=True, exist_ok=True)
            for path in case_dir.iterdir():
                if path.is_file() and (upload_xml or path.suffix.lower() != ".xml"):
                    shutil.copy2(path, target_dir / path.name)
    else:
        raise RuntimeError("qiniu_upload_scope 只支持 failed_only 或 all")

    return publish_dir


def upload_report_to_qiniu(bundle_dir, config, failed_cases):
    validate_qiniu_config(config)
    publish_dir = copy_qiniu_publish_bundle(bundle_dir, config, failed_cases)
    prefix = normalize_qiniu_prefix(config.get("qiniu_prefix"))
    base_key = "/".join(part for part in (prefix, bundle_dir.name) if part)

    files = sorted(path for path in publish_dir.rglob("*") if path.is_file())
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(publish_dir).as_posix()
        key = f"{base_key}/{relative}" if base_key else relative
        print(f"Uploading report file to Qiniu ({index}/{len(files)}): {key}")
        qiniu_upload_file(config, path, key)

    report_key = f"{base_key}/recording_report.html" if base_key else "recording_report.html"
    domain = str(config["qiniu_domain"]).strip().rstrip("/")
    return f"{domain}/{urllib.parse.quote(report_key, safe='/')}"


def signed_webhook(config):
    webhook = config.get("dingtalk_webhook", "")
    secret = config.get("dingtalk_secret", "")
    if not webhook or not secret:
        return webhook

    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    secret_bytes = secret.encode("utf-8")
    digest = hmac.new(secret_bytes, string_to_sign, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    separator = "&" if "?" in webhook else "?"
    return f"{webhook}{separator}timestamp={timestamp}&sign={sign}"


def dingtalk_markdown(config, manifest, link, exit_code, failed_cases=None, report_note=None):
    total = int(manifest.get("total", 0))
    passed = int(manifest.get("passed", 0))
    failed = int(manifest.get("failed", 0)) + int(manifest.get("errors", 0))
    skipped = int(manifest.get("skipped", 0))
    pass_rate = f"{(passed / total * 100):.2f}%" if total else "0.00%"
    generated_at = manifest.get("generated_at") or now_local().strftime("%Y-%m-%d %H:%M:%S")
    report_id = manifest.get("report_id", "")
    report_title = config.get("dingtalk_report_title", "Auro AI 自动化测试报告")
    keyword = config.get("dingtalk_keyword", "test")

    attention_lines = []
    if failed_cases:
        attention_lines.append("")
        attention_lines.append(f"需关注用例（前 {len(failed_cases)} 条）：")
        for item in failed_cases:
            attention_lines.append(f"- {item['name']} [{item['status']}]")

    title = f"[{keyword}] {report_title}"
    lines = [
        f"### {title}",
        f"生成时间：{generated_at}",
        f"- 总用例：{total}",
        f"- ✅ 通过：{passed}",
        f"- ⚠ 待复核：{skipped}",
        f"- ❌ 失败：{failed}",
        f"- 📊 通过率：{pass_rate}",
        *attention_lines,
        "",
        f"📊 [点击查看完整报告]({link})",
    ]
    if report_note:
        lines.append(f"报告链路说明：{report_note}")
    if report_id:
        lines.append(f"归档编号：{report_id}")

    text = "\n".join(lines)
    return {"msgtype": "markdown", "markdown": {"title": title, "text": text}}


def send_dingtalk(config, payload):
    webhook = signed_webhook(config)
    if not webhook:
        raise RuntimeError("未配置 dingtalk_webhook")

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"钉钉推送失败 HTTP {response.status}: {body}")
        result = json.loads(body)
        if result.get("errcode") != 0:
            raise RuntimeError(f"钉钉推送失败: {result}")
        return result


def run_pytest(config):
    python_exe = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)

    env = os.environ.copy()
    env.pop("RECORDING_FILE", None)
    env["APPIUM_COMMAND"] = str(config.get("appium_command") or "appium.cmd")
    env["APPIUM_ARGS_JSON"] = json.dumps(config.get("appium_args", []), ensure_ascii=False)
    env["ANDROID_ADB"] = str(config.get("adb_exe") or "adb")
    legacy_udid = str(config.get("android_udid") or "")
    env["ANDROID_UDID"] = legacy_udid
    env["ANDROID_ADB_SERIAL"] = str(adb_serial(config) or "")
    env["APPIUM_UDID"] = str(config.get("appium_udid") or legacy_udid)
    env["APPIUM_SERVER"] = str(config.get("appium_server") or "http://127.0.0.1:4725/appium-mvp")
    env["APPIUM_AUTO_RESTART"] = "1" if config.get("appium_auto_restart", True) else "0"
    env["APP_PACKAGE"] = str(config.get("app_package") or "com.moyoung.auro.ai")
    env["APP_ACTIVITY"] = str(config.get("app_activity") or ".MainActivity")
    env["AUDIO_INJECTION_MODE"] = str(config.get("audio_injection_mode") or "host")
    env["DEVICE_FARM_DOCKER_COMMAND"] = str(config.get("device_farm_docker_command") or "docker")
    env["DEVICE_FARM_SSH_TARGET"] = str(config.get("device_farm_ssh_target") or "")
    env["DEVICE_FARM_EMULATOR_CONTAINER"] = str(config.get("device_farm_emulator_container") or "")
    env["DEVICE_FARM_PULSE_SERVER"] = str(config.get("device_farm_pulse_server") or "tcp:127.0.0.1:4713")
    env["DEVICE_FARM_PULSE_SINK"] = str(config.get("device_farm_pulse_sink") or "virtual_mic")
    env["ANDROID_EMULATOR"] = str(config.get("emulator_exe") or "")
    env["ANDROID_AVD"] = str(config.get("emulator_avd") or "")
    env["ANDROID_EMULATOR_EXTRA_ARGS"] = " ".join(
        str(arg) for arg in config.get("emulator_extra_args", [])
    )
    env["ANDROID_BOOT_TIMEOUT_SECONDS"] = str(config.get("emulator_boot_timeout_seconds", 240))
    env["ANDROID_POST_BOOT_WAIT_SECONDS"] = str(config.get("emulator_post_boot_wait_seconds", 25))
    env["ANDROID_AUTO_START_EMULATOR"] = "1" if config.get("start_emulator_if_missing", True) else "0"
    env["ANDROID_ALLOW_HOST_AUDIO"] = "1" if config.get("emulator_allow_host_audio", True) else "0"
    env["ANDROID_AUDIO_BACKEND"] = str(config.get("emulator_audio_backend") or "dsound")
    env["AURO_AUTO_LOGIN"] = "1" if config.get("auro_auto_login", True) else "0"
    if config.get("auro_login_account"):
        env["AURO_LOGIN_ACCOUNT"] = str(config["auro_login_account"])
    if config.get("auro_login_password"):
        env["AURO_LOGIN_PASSWORD"] = str(config["auro_login_password"])
    # pytest 的用例名/断言里含大量中文。本机 locale 是 cp936，子进程默认按 GBK
    # 输出，而 pytest 实际写的是 UTF-8，父进程再按 GBK 解码就会得到
    # `鐐瑰嚮棣栭〉` 这种乱码，夜跑日志完全没法排查。这里显式统一成 UTF-8：
    # 让子进程按 UTF-8 输出（PYTHONUTF8/PYTHONIOENCODING），父进程也按 UTF-8 解码。
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    command = [
        str(python_exe),
        "-m",
        "pytest",
        "-v",
        str(PROJECT_ROOT / "tests" / "test_run_recordings.py"),
    ]
    return run(
        command,
        timeout=int(config["pytest_timeout_seconds"]),
        env=env,
        text_encoding="utf-8",
    )


def write_run_log(result):
    log_dir = PROJECT_ROOT / "reports" / "nightly_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_local().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"nightly_{stamp}.log"
    log_path.write_text(
        "\n".join([
            f"exit_code={result.returncode}",
            "===== STDOUT =====",
            result.stdout or "",
            "===== STDERR =====",
            result.stderr or "",
        ]),
        encoding="utf-8",
    )
    return log_path


# 脏前台（分享面板等）残留会让 Appium 在 create_driver 阶段就报
# "Cannot start the ... application / ... never started"，一次残留会连带
# 拖垮后面整批用例。这里显式报警，避免这类回归静默混进夜跑结论。
CASCADE_SIGNATURES = (
    "Cannot start the",
    "never started",
)


def summarize_cascade_risk(result):
    """Return (count, sample_line) for the dirty-foreground cascade signature."""
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    hits = sum(text.count(sig) for sig in CASCADE_SIGNATURES)
    sample = ""
    if hits:
        for line in text.splitlines():
            if any(sig in line for sig in CASCADE_SIGNATURES):
                sample = line.strip()
                break
    return hits, sample


# 夜跑是在睡眠唤醒后无人值守执行：此时没有可用的交互式桌面会话，
# 模拟器的 DirectSound 后端初始化失败（stderr 打印
# "dsound: Could not set cooperative level for window 0000000000000000"），
# 宿主音频回环（Windows 播放设备 -> CABLE Input -> 模拟器 host mic）随之
# 送不出声音，App 收不到语音就一直停在「未连接」，5 条 P0 翻译用例
# [翻译]单向模式_*、[翻译]手机模式_点击录音_* 必挂且重试无用。
#
# README「音频方案」写明这是主机虚拟声卡方案，依赖
# "-allow-host-audio -audio dsound" 且 Windows 播放设备路由到 VB-CABLE。
# 同一台机器白天人工跑能全绿、夜里固定挂这 5 条，差别就是交互式会话。
EMULATOR_AUDIO_FAILURE_SIGNATURES = (
    "Could not set cooperative level",
    "dsound:",
)
NO_INTERACTIVE_SESSION_WINDOW = "window 0000000000000000"


def emulator_audio_health(config):
    """Report whether the emulator host-audio bridge came up on this launch.

    Reads the stderr that start_detached() already appends for the emulator,
    so this adds no new capture mechanism.  Returns (ok, detail).
    """
    if str(config.get("audio_injection_mode") or "host").strip().lower() != "host":
        return True, "音频注入非 host 模式，跳过主机声卡检查。"

    audio_backend = str(config.get("emulator_audio_backend") or "").strip().lower()
    if audio_backend != "dsound":
        return True, f"音频后端为 {audio_backend or '<空>'}，跳过 DirectSound 检查。"

    log_path = REPORT_ROOT / "emulator_stderr.log"
    if not log_path.exists():
        return True, "未找到 emulator_stderr.log，无法判断音频桥接状态。"

    recent = log_path.read_text(encoding="utf-8", errors="replace")[-20000:]
    hits = [sig for sig in EMULATOR_AUDIO_FAILURE_SIGNATURES if sig in recent]
    if not hits:
        return True, "模拟器 stderr 未出现 DirectSound 初始化失败。"

    window_missing = NO_INTERACTIVE_SESSION_WINDOW in recent
    detail = (
        "模拟器 DirectSound 后端初始化失败"
        f"（命中 {', '.join(hits)}）"
        + (
            "；窗口句柄为 0，说明当前没有可用的交互式桌面会话"
            if window_missing
            else ""
        )
    )
    return False, detail


def emulator_host_mic_reachable(config):
    """Probe the documented host-mic bridge path, mirroring core/audio.py.

    Uses the same `adb emu avd hostmicoff/hostmicon` toggle the runner relies
    on, so a failure here means the bridge path itself is unavailable.
    """
    udid = str(adb_serial(config) or "")
    if not udid.startswith("emulator-"):
        return True, "目标不是模拟器，跳过 host mic 桥接探测。"

    for command in ("hostmicoff", "hostmicon"):
        result = adb_target(config, ["emu", "avd", command], timeout=15)
        if result.returncode != 0:
            message = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
            return False, f"模拟器控制台拒绝 {command}: {message}"
    return True, "host mic 桥接命令可用。"


def check_audio_preconditions(config):
    """Fail loud before the nightly burns ~50 minutes on doomed audio cases."""
    ok, detail = emulator_audio_health(config)
    if not ok:
        print(f"[AUDIO-PRECHECK] 主机音频回环不可用: {detail}")
        print(
            "[AUDIO-PRECHECK] 夜跑很可能在唤醒后无交互式会话的情况下执行；"
            "依赖音频注入的 [翻译]用例预期会失败（表现为一直停在「未连接」）。"
            "请确认唤醒后存在可用桌面会话，且 Windows 播放设备已路由到 VB-CABLE。"
        )
        return False

    mic_ok, mic_detail = emulator_host_mic_reachable(config)
    if not mic_ok:
        print(f"[AUDIO-PRECHECK] host mic 桥接探测失败: {mic_detail}")
        return False

    print(f"[AUDIO-PRECHECK] 音频前置检查通过: {detail} {mic_detail}")
    return True


def write_fatal_log(exc):
    log_dir = PROJECT_ROOT / "reports" / "nightly_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_local().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"nightly_{stamp}_fatal.log"
    log_path.write_text(
        "\n".join([
            f"exit_code=1",
            f"fatal_at={now_local().strftime('%Y-%m-%d %H:%M:%S')}",
            f"exception={type(exc).__name__}: {exc}",
            "===== TRACEBACK =====",
            traceback.format_exc(),
        ]),
        encoding="utf-8",
    )
    return log_path


@contextlib.contextmanager
def single_instance_lock():
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = REPORT_ROOT / "nightly_run.lock"
    lock_file = lock_path.open("a+", encoding="utf-8")
    acquired = False

    try:
        if os.name == "nt":
            import msvcrt

            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                print(f"Another nightly run is already in progress: {lock_path}")
                yield False
                return
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"Another nightly run is already in progress: {lock_path}")
                yield False
                return

        acquired = True
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": now_local().strftime("%Y-%m-%d %H:%M:%S"),
                },
                ensure_ascii=False,
            )
        )
        lock_file.flush()
        yield True
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def main():
    parser = argparse.ArgumentParser(description="Run nightly Appium recordings and notify DingTalk.")
    parser.add_argument("--notify", choices=["auto", "always", "never"], default="auto")
    args = parser.parse_args()

    with single_instance_lock() as should_run:
        if not should_run:
            return 0

        try:
            return run_nightly(args)
        except Exception as exc:
            log_path = write_fatal_log(exc)
            print(f"Nightly run failed; fatal log written to {log_path}")
            raise


def run_nightly(args):
    config = load_config()
    report_base_url = None
    if not qiniu_requested(config):
        report_base_url = ensure_report_server(config)
    clean_runtime_before_run(config)
    ensure_emulator(config)
    register_runtime_cleanup(config)
    ensure_target_app(config)
    ensure_appium(config)

    audio_ready = check_audio_preconditions(config)

    result = run_pytest(config)
    log_path = write_run_log(result)

    if not audio_ready:
        print(
            "[AUDIO-PRECHECK] 本次夜跑在音频前置检查失败的情况下执行，"
            "依赖音频注入的 [翻译] 用例失败属于环境噪声，不要当作产品回归。"
        )

    cascade_hits, cascade_sample = summarize_cascade_risk(result)
    if cascade_hits:
        print(
            f"[CASCADE-RISK] 检测到 {cascade_hits} 处脏前台连带失败特征"
            f"（分享面板未关闭之类的残留会拖垮后续整批用例）: {cascade_sample}"
        )
    else:
        print("[CASCADE-RISK] 未检测到脏前台连带失败特征。")

    bundle = latest_report_bundle()
    if not bundle:
        raise RuntimeError("没有找到 recording_report_bundle_*，无法生成钉钉报告链接")

    manifest = read_manifest(bundle)
    failed_cases = failed_cases_from_report(bundle, limit=None)
    report_note = None
    if qiniu_requested(config):
        try:
            link = upload_report_to_qiniu(bundle, config, failed_cases)
        except Exception as exc:
            if not bool(config.get("qiniu_fallback_to_local_report", True)):
                raise
            print(
                "Qiniu report upload failed; falling back to local report link: "
                f"{type(exc).__name__}: {exc}"
            )
            report_base_url = report_base_url or ensure_report_server(config)
            link = report_link(bundle, report_base_url)
            report_note = f"七牛上传失败，已降级为本机报告链接（{type(exc).__name__}: {exc}）"
    else:
        link = report_link(bundle, report_base_url)
    payload = dingtalk_markdown(
        config,
        manifest,
        link,
        result.returncode,
        failed_cases=failed_cases[:8],
        report_note=report_note,
    )

    should_send = args.notify == "always" or (
        args.notify == "auto" and within_send_window(config)
    )
    if should_send:
        send_dingtalk(config, payload)
        print("DingTalk notification sent.")
    else:
        print("DingTalk notification skipped outside allowed window.")
        print(json.dumps(payload, ensure_ascii=True, indent=2))

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
