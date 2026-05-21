import argparse
import atexit
import base64
import contextlib
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
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
    "appium_args": ["--address", "0.0.0.0", "--port", "4723"],
    "start_emulator_if_missing": True,
    "stop_emulator_after_run": True,
    "emulator_exe": r"E:\android_sdk\emulator\emulator.exe",
    "emulator_avd": "Pixel_8a",
    "emulator_allow_host_audio": True,
    "emulator_audio_backend": "dsound",
    "emulator_extra_args": ["-no-snapshot-load"],
    "auro_auto_login": True,
    "auro_login_account": "",
    "auro_login_password": "",
    "adb_exe": r"E:\android_sdk\platform-tools\adb.exe",
    "android_udid": "emulator-5554",
    "emulator_boot_timeout_seconds": 240,
    "emulator_post_boot_wait_seconds": 25,
    "pytest_timeout_seconds": 14400,
}


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


def run(command, timeout=None, env=None):
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
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
    return os.environ.get("APPIUM_SERVER", "http://127.0.0.1:4723").rstrip("/") + "/status"


def ensure_appium(config):
    if url_ok(appium_status_url()):
        return
    if not config.get("start_appium_if_missing", True):
        return
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
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def adb_shell(config, args, timeout=10):
    adb = config.get("adb_exe") or "adb"
    udid = config.get("android_udid")
    command = [adb]
    if udid:
        command.extend(["-s", udid])
    command.extend(["shell", *args])
    return run(command, timeout=timeout)


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
    udid = config.get("android_udid")
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


def kill_emulator_processes():
    if os.name != "nt":
        return
    run(["taskkill", "/IM", "emulator.exe", "/F"], timeout=20)
    run(["taskkill", "/IM", "qemu-system-x86_64.exe", "/F"], timeout=20)


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
        return 4723


def pids_listening_on_port(port):
    if os.name != "nt":
        return []

    result = run(["netstat", "-ano", "-p", "tcp"], timeout=10)
    pids = set()
    marker = f":{port}"
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[1].endswith(marker) and parts[3].upper() == "LISTENING" and parts[-1].isdigit():
            pids.add(parts[-1])
    return sorted(pids)


def stop_appium(config):
    if not config.get("stop_appium_after_run", True):
        return

    for pid in pids_listening_on_port(appium_port_from_config(config)):
        run(["taskkill", "/PID", pid, "/F"], timeout=20)


def clean_runtime_before_run(config):
    if not config.get("clean_runtime_before_run", True):
        return

    print("Cleaning stale Appium/emulator runtime before nightly run.")
    for pid in pids_listening_on_port(appium_port_from_config(config)):
        run(["taskkill", "/PID", pid, "/F"], timeout=20)
    kill_emulator_processes()
    adb = config.get("adb_exe") or "adb"
    run([adb, "kill-server"], timeout=15)
    run([adb, "start-server"], timeout=15)


def stop_emulator(config):
    if not config.get("stop_emulator_after_run", True):
        return

    adb = config.get("adb_exe") or "adb"
    udid = config.get("android_udid")
    if udid and udid in adb_devices(config):
        run([adb, "-s", udid, "emu", "kill"], timeout=20)
        time.sleep(2)

    kill_emulator_processes()


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
    udid = config.get("android_udid")
    devices = adb_devices(config)
    if (udid and udid in devices) or (not udid and devices):
        if wait_for_device_ready(config, timeout_seconds=60):
            return
        if not config.get("start_emulator_if_missing", True):
            return
        print("Existing Android emulator is listed but not responsive; restarting it.")
        kill_emulator_processes()
        run([config.get("adb_exe") or "adb", "kill-server"], timeout=15)
        run([config.get("adb_exe") or "adb", "start-server"], timeout=15)

    if not config.get("start_emulator_if_missing", True):
        return

    emulator = config.get("emulator_exe")
    avd = config.get("emulator_avd")
    if not emulator or not avd or not Path(emulator).exists():
        return

    run([config.get("adb_exe") or "adb", "kill-server"], timeout=15)
    run([config.get("adb_exe") or "adb", "start-server"], timeout=15)

    command = [emulator, "-avd", avd]
    if config.get("emulator_allow_host_audio", True):
        command.append("-allow-host-audio")
    audio_backend = str(config.get("emulator_audio_backend") or "").strip()
    if audio_backend:
        command.extend(["-audio", audio_backend])
    command.extend(str(arg) for arg in config.get("emulator_extra_args", []))

    start_detached(command)
    if wait_for_device_ready(config):
        return

    print("Android emulator did not become ready after first launch; retrying without snapshot.")
    kill_emulator_processes()
    run([config.get("adb_exe") or "adb", "kill-server"], timeout=15)
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
        if len(cases) >= limit:
            break
    return cases


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


def dingtalk_markdown(config, manifest, link, exit_code, failed_cases=None):
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
    env["ANDROID_UDID"] = str(config.get("android_udid") or "")
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
    command = [
        str(python_exe),
        "-m",
        "pytest",
        "-v",
        str(PROJECT_ROOT / "tests" / "test_run_recordings.py"),
    ]
    return run(command, timeout=int(config["pytest_timeout_seconds"]), env=env)


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
    report_base_url = ensure_report_server(config)
    clean_runtime_before_run(config)
    ensure_emulator(config)
    ensure_appium(config)
    register_runtime_cleanup(config)

    result = run_pytest(config)
    write_run_log(result)

    bundle = latest_report_bundle()
    if not bundle:
        raise RuntimeError("没有找到 recording_report_bundle_*，无法生成钉钉报告链接")

    manifest = read_manifest(bundle)
    link = report_link(bundle, report_base_url)
    payload = dingtalk_markdown(
        config,
        manifest,
        link,
        result.returncode,
        failed_cases=failed_cases_from_report(bundle),
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
