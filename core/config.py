import json
import os
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows fallback
    winreg = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG_PATH = PROJECT_ROOT / "scripts" / "nightly_config.local.json"


def load_local_config() -> dict:
    if not LOCAL_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


LOCAL_CONFIG = load_local_config()


def env_or_local(env_name: str, local_name: str, default=""):
    value = os.environ.get(env_name)
    if value not in (None, ""):
        return value
    return LOCAL_CONFIG.get(local_name, default)


def bool_env_or_local(env_name: str, local_name: str, default=True) -> bool:
    value = os.environ.get(env_name)
    if value not in (None, ""):
        return value.lower() not in {"0", "false", "no"}
    if local_name in LOCAL_CONFIG:
        value = LOCAL_CONFIG[local_name]
        if isinstance(value, bool):
            return value
        return str(value).lower() not in {"0", "false", "no"}
    return default

APPIUM_SERVER = os.environ.get("APPIUM_SERVER", "http://127.0.0.1:4723")
ANDROID_UDID = os.environ.get("ANDROID_UDID", "emulator-5554")
ANDROID_ADB = os.environ.get("ANDROID_ADB", r"E:\android_sdk\platform-tools\adb.exe")
ANDROID_EMULATOR = os.environ.get("ANDROID_EMULATOR", r"E:\android_sdk\emulator\emulator.exe")
ANDROID_AVD = os.environ.get("ANDROID_AVD", "Pixel_8a")
ANDROID_EMULATOR_EXTRA_ARGS = os.environ.get("ANDROID_EMULATOR_EXTRA_ARGS", "-no-snapshot-load")
ANDROID_AUDIO_BACKEND = os.environ.get("ANDROID_AUDIO_BACKEND", "dsound")
ANDROID_BOOT_TIMEOUT_SECONDS = int(os.environ.get("ANDROID_BOOT_TIMEOUT_SECONDS", "240"))
ANDROID_POST_BOOT_WAIT_SECONDS = float(os.environ.get("ANDROID_POST_BOOT_WAIT_SECONDS", "0"))
ANDROID_AUTO_START_EMULATOR = os.environ.get("ANDROID_AUTO_START_EMULATOR", "1").lower() not in {"0", "false", "no"}
ANDROID_ALLOW_HOST_AUDIO = os.environ.get("ANDROID_ALLOW_HOST_AUDIO", "1").lower() not in {"0", "false", "no"}
APP_PACKAGE = os.environ.get("APP_PACKAGE", "com.moyoung.auro.ai")
APP_ACTIVITY = os.environ.get("APP_ACTIVITY", ".MainActivity")
LAUNCHER_PACKAGE = os.environ.get("LAUNCHER_PACKAGE", "com.google.android.apps.nexuslauncher")
LAUNCHER_ACTIVITY = os.environ.get("LAUNCHER_ACTIVITY", ".NexusLauncherActivity")
DEFAULT_TIMEOUT = int(os.environ.get("DEFAULT_TIMEOUT", "30"))
AUDIO_SETTLE_SECONDS = float(os.environ.get("AUDIO_SETTLE_SECONDS", "4"))
AUDIO_START_DELAY_SECONDS = float(os.environ.get("AUDIO_START_DELAY_SECONDS", "2"))
AURO_AUTO_LOGIN = bool_env_or_local("AURO_AUTO_LOGIN", "auro_auto_login", True)
AURO_LOGIN_ACCOUNT = env_or_local("AURO_LOGIN_ACCOUNT", "auro_login_account", "")
AURO_LOGIN_PASSWORD = env_or_local("AURO_LOGIN_PASSWORD", "auro_login_password", "")


def load_model_api_key() -> str:
    api_key = os.getenv("ALI_BAICHUAN_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
    if api_key or not winreg:
        return api_key

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            return winreg.QueryValueEx(key, "ALI_BAICHUAN_API_KEY")[0]
    except OSError:
        return ""


ALI_BAICHUAN_API_KEY = load_model_api_key()
BASE_URL = os.getenv("ALIYUN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

# Unified model entrypoints controlled by config.
MODEL_VISION_ANALYSIS = os.getenv("MODEL_VISION_ANALYSIS", "qwen3.5-plus")
MODEL_VISION_FAST = os.getenv("MODEL_VISION_FAST", "qwen3-vl-flash")
MODEL_TEXT = os.getenv("MODEL_TEXT", "qwen3.5-flash")
MODEL_TEST_CASE_GENERATION = os.getenv("MODEL_TEST_CASE_GENERATION", "qwen3.6-plus")

# Backward-compatible aliases for existing callers.
ALIYUN_BASE_URL = BASE_URL
ALIYUN_MODEL = os.getenv("ALIYUN_MODEL", MODEL_TEXT)
ALIYUN_API_KEY = ALI_BAICHUAN_API_KEY
