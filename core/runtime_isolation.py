import os
import locale
import subprocess


APPIUM_BASE_PATH = "/appium-mvp"
SUBPROCESS_ENCODING = locale.getencoding()


def pids_listening_on_port(port):
    """Return Windows listener PIDs without making any process changes."""
    if os.name != "nt":
        return []

    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding=SUBPROCESS_ENCODING,
        errors="replace",
        timeout=10,
        check=False,
    )
    pids = set()
    marker = f":{int(port)}"
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[1].endswith(marker) and parts[3].upper() == "LISTENING" and parts[-1].isdigit():
            pids.add(parts[-1])
    return sorted(pids)


def process_command_line(pid):
    """Read a process command line so ownership can be checked before termination."""
    if os.name != "nt" or not str(pid).isdigit():
        return ""

    script = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\"; "
        "if ($null -ne $p) { $p.CommandLine }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding=SUBPROCESS_ENCODING,
        errors="replace",
        timeout=10,
        check=False,
    )
    return (result.stdout or "").strip()


def appium_process_is_owned(pid, port, command_line=None):
    """Accept only Appium launched with this project's port and unique base path."""
    command_line = command_line if command_line is not None else process_command_line(pid)
    normalized = " ".join(str(command_line).lower().replace('"', "").split())
    port_markers = (f"--port {int(port)}", f"--port={int(port)}")
    path_markers = (
        f"--base-path {APPIUM_BASE_PATH}",
        f"--base-path={APPIUM_BASE_PATH}",
    )
    return (
        "appium" in normalized
        and any(marker in normalized for marker in port_markers)
        and any(marker in normalized for marker in path_markers)
    )


def owned_appium_pids_on_port(port):
    return [pid for pid in pids_listening_on_port(port) if appium_process_is_owned(pid, port)]
