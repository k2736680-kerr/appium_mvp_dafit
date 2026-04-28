import json
import shutil
import subprocess
import urllib.error
import urllib.request

from appium import webdriver
from appium.options.android import UiAutomator2Options

from core.config import (
    ANDROID_ADB,
    ANDROID_UDID,
    APPIUM_SERVER,
    APP_ACTIVITY,
    APP_PACKAGE,
    LAUNCHER_ACTIVITY,
    LAUNCHER_PACKAGE,
)


def _adb_command():
    if ANDROID_ADB and shutil.which(ANDROID_ADB):
        return ANDROID_ADB
    if ANDROID_ADB and "\\" in ANDROID_ADB and shutil.which("adb"):
        return "adb"
    return ANDROID_ADB or "adb"


def assert_appium_server_running():
    status_url = APPIUM_SERVER.rstrip("/") + "/status"
    try:
        with urllib.request.urlopen(status_url, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Appium Server 未启动或无法连接。\n"
            f"当前地址：{APPIUM_SERVER}\n"
            "请先另开一个 CMD 窗口执行：\n"
            "appium --address 0.0.0.0 --port 4723\n"
            f"原始错误：{exc}"
        )

    if not isinstance(data, dict):
        raise RuntimeError(f"Appium Server 状态返回异常：{data}")


def assert_android_device_connected():
    adb = _adb_command()
    try:
        result = subprocess.run(
            [adb, "devices"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        raise RuntimeError(
            "Android 设备前置检查失败：无法执行 adb devices。\n"
            f"当前 adb 路径：{adb}\n"
            "请检查 Android SDK/ADB 是否可用。\n"
            f"原始错误：{exc}"
        )

    output = result.stdout or ""
    connected_devices = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            connected_devices.append(parts[0])

    if ANDROID_UDID in connected_devices:
        return

    if connected_devices and not ANDROID_UDID:
        return

    raise RuntimeError(
        "没有检测到可用的 Android 设备，App 还不能启动。\n"
        f"期望设备：{ANDROID_UDID}\n"
        f"adb 输出：\n{output.strip() or '(空)'}\n\n"
        "请先启动模拟器，并确认 adb devices 显示 emulator-5554    device。\n"
        '启动命令示例：start "" "E:\\android_sdk\\emulator\\emulator.exe" -avd Pixel_8a'
    )


def assert_runtime_ready():
    assert_appium_server_running()
    assert_android_device_connected()


def create_driver(start_mode="app", no_reset=True):
    """
    start_mode:
      - app: 直接启动被测 App
      - launcher: 启动桌面，适合回放 Inspector 第一
        步从桌面 Auro AI 图标开始的录制代码
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

    options = UiAutomator2Options().load_capabilities(caps)
    return webdriver.Remote(APPIUM_SERVER, options=options)
