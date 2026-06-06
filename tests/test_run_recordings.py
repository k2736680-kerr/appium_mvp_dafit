import ast
import os
import re
import time
from pathlib import Path

import pytest

from core.driver_factory import (
    create_driver,
    is_transient_session_error,
    quit_driver_safely,
    restart_appium_server,
)
from core.recording_rules import augment_recording_steps, parse_case_labels
from core.runner import CaseRunner, DEFAULT_BLOCKERS
from core.config import PROJECT_ROOT


APP_PACKAGE = "com.moyoung.auro.ai"
RECORDINGS_DIR = PROJECT_ROOT / "recordings"
APP_RESTARTED_ONCE = False
HOME_MARKER = "翻译中心"

# 协议类页面正文中常出现「网络异常」等字样，全局子串会误判为阻断。
_REGULATION_TITLE_MARKERS = ("服务协议", "隐私政策", "用户协议")


def blockers_for_recording_title(clean_title: str) -> list[str]:
    blockers = list(DEFAULT_BLOCKERS)
    if any(marker in clean_title for marker in _REGULATION_TITLE_MARKERS):
        blockers = [b for b in blockers if b != "网络异常"]
    return blockers


def safe_case_id(name: str) -> str:
    result = []
    for ch in name:
        if ch.isalnum() or ch in "_-":
            result.append(ch)
        else:
            result.append("_")
    return "".join(result).strip("_") or "recording_case"


def unquote_python_string(value: str) -> str:
    try:
        return ast.literal_eval(value)
    except Exception:
        return value.strip("\"'")


def map_by(appium_by: str) -> str:
    mapping = {
        "ACCESSIBILITY_ID": "accessibility_id",
        "ANDROID_UIAUTOMATOR": "android_uiautomator",
        "ID": "id",
        "XPATH": "xpath",
    }
    return mapping.get(appium_by, appium_by.lower())


def step_name_from_locator(locator: dict, index: int) -> str:
    by = locator.get("by")
    value = locator.get("value", "")

    if by == "accessibility_id":
        return f"点击：{value}"

    if "description(" in value:
        m = re.search(r'description\("(.+?)"\)', value, re.S)
        if m:
            text = m.group(1).replace("\\n", " / ").replace("\n", " / ")
            return f"点击：{text}"

    if "descriptionContains(" in value:
        m = re.search(r'descriptionContains\("(.+?)"\)', value, re.S)
        if m:
            return f"点击：{m.group(1)}"

    if "instance(" in value:
        return f"点击录制元素 instance：第 {index} 步"

    return f"点击录制元素：第 {index} 步"


def step_name_from_swipe(start_x: int, start_y: int, end_x: int, end_y: int) -> str:
    dx = end_x - start_x
    dy = end_y - start_y

    if abs(dy) >= abs(dx):
        direction = "上滑" if dy < 0 else "下滑"
    else:
        direction = "左滑" if dx < 0 else "右滑"

    return f"滑动：{direction} ({start_x},{start_y}) -> ({end_x},{end_y})"


def step_name_from_tap(x: int, y: int) -> str:
    return f"点击坐标：({x},{y})"


def wait_after_click(locator: dict, title: str = "") -> float:
    by = locator.get("by")
    value = locator.get("value", "")

    # 个人资料类用例点“提交”后，页面会短暂展示“保存成功”并禁用控件。
    # 录制脚本下一步通常会马上再次进入账户页或点击头像，太快会被吞点击。
    if by == "accessibility_id" and value == "提交":
        if is_profile_case(title):
            return 4

    return 1


def is_profile_case(title: str) -> bool:
    profile_titles = ("修改生日", "修改用户名", "切换性别", "切换头像")
    return any(name in title for name in profile_titles)


LANGUAGE_OPTION_LABELS = {
    "中文",
    "中文（简体）",
    "English",
    "日本語",
    "한국어",
}

DYNAMIC_SETTING_FIELDS = {
    "语音播报语速",
}


def exact_description_lines(value: str) -> list[str]:
    match = re.search(r'description\("(.+?)"\)', value, re.S)
    if not match:
        return []
    description = match.group(1).replace("\\n", "\n")
    return [line.strip() for line in description.split("\n") if line.strip()]


def description_contains_click_step(
    field_name: str,
    name: str,
    title: str = "",
    fallback_tap: dict | None = None,
):
    locator = {
        "by": "android_uiautomator",
        "value": f'new UiSelector().descriptionContains("{field_name}")',
    }
    step = {
        "action": "click",
        "name": name,
        "locator": locator,
        "timeout": 30,
        "wait_after": wait_after_click(locator, title),
    }
    if fallback_tap:
        step["fallback_tap"] = fallback_tap
    return step


def convert_known_locator_to_point(locator: dict, index: int, title: str = ""):
    """
    把已知的、容易不稳定的 Inspector 定位，自动转成坐标点击。

    注意：
    - 这里不是让你手写每条用例；
    - 这是把我们已经验证过的固定控件沉淀成公共规则；
    - 后面发现新的公共控件，再往这里加一条规则即可。
    """
    by = locator.get("by")
    value = locator.get("value", "")

    if "登出和登录" in title and (
        by == "android_uiautomator"
        and 'className("android.widget.Button").instance(0)' in value
    ):
        return {
            "action": "hide_keyboard",
            "name": "收起登录页键盘",
            "wait_after": 1,
        }

    # 个人资料类用例经常会在“提交”后重新进入“我的账户”。
    # 入口在不同状态下纵向位置会变，执行层优先按文案动态定位，再保留重试能力。
    if is_profile_case(title) and by == "accessibility_id" and value == "我的账户":
        return {
            "action": "tap_my_account",
            "name": "进入我的账户",
            "attempts": 6,
            "attempt_wait": 1.5,
            "wait_after": 2,
        }

    # 语言列表、设置项这类控件经常是“名称\n当前值/英文名”，回放时当前值会变化。
    # 统一弱化到稳定首行，避免每个页面单独改录制脚本。
    description_lines = exact_description_lines(value)
    if len(description_lines) >= 2:
        stable_name = description_lines[0]
        if stable_name in LANGUAGE_OPTION_LABELS:
            return description_contains_click_step(
                stable_name,
                f"点击语言：{stable_name}",
                title,
            )
        if stable_name in DYNAMIC_SETTING_FIELDS:
            return description_contains_click_step(
                stable_name,
                f"点击设置项：{stable_name}",
                title,
            )

    # 个人资料页字段的值会被前序用例修改，不能依赖录制时的完整
    # “字段名\n当前值”。统一弱化为 descriptionContains(字段名)。
    for profile_field in ("用户名", "生日", "性别"):
        if f'description("{profile_field}\\n' in value or f'description("{profile_field}\n' in value:
            return {
                "action": "click",
                "name": f"点击个人资料字段：{profile_field}",
                "locator": {
                    "by": "android_uiautomator",
                    "value": f'new UiSelector().descriptionContains("{profile_field}")',
                },
                "timeout": 30,
                "wait_after": 1,
            }

    # 头像 ImageView 没有稳定文案，class_name 命中容易漂。
    # 我的账户页头像中心在 1080x2400 下约 (540, 489)。
    if "切换头像" in title and by == "class_name" and value == "android.widget.ImageView":
        return {
            "action": "tap_point",
            "name": "点击我的账户-头像",
            "x": 540,
            "y": 489,
            "wait_after": 2,
        }

    if (
        "相册" in title
        and by == "android_uiautomator"
        and 'className("android.view.View").instance(24)' in value
    ):
        return {
            "action": "tap_first_album_photo",
            "name": "点击 Google 相册第一张图片",
            "description_contains": ["照片拍摄于", "Photo taken"],
            "fallback_tap": {"x": 179, "y": 1354},
            "timeout": 15,
            "wait_after": 1,
        }

    if (
        "相册" in title
        and by == "android_uiautomator"
        and 'className("android.widget.Button").instance(6)' in value
    ):
        return {
            "action": "tap_text_center",
            "name": "点击 Google 相册完成",
            "texts": ["完成", "Done"],
            "fallback_tap": {"x": 913, "y": 2202},
            "timeout": 15,
            "wait_after": 2,
        }

    # 会议记录页底部录制按钮。
    # Inspector 会导出 instance(7)，但实际回放时这个弱定位不稳定，容易没有点到
    # 底部录制按钮，导致后续找不到“结束录制”。
    if "会议记录" in title and (
        'className("android.view.View").instance(7)' in value
        or 'className("android.view.View").instance(11)' in value
    ):
        return {
            "action": "tap_point",
            "name": "点击会议记录-底部录制按钮",
            "x": 540,
            "y": 2169,
            "wait_after": 1
        }

    # 会议记录名称是按生成时间变化的，不要依赖录制时的固定标题。
    # 先按标题前缀找当前记录，找不到再点当前详情页标题区域。
    if (
        "会议记录_修改名称" in title
        and by == "accessibility_id"
        and value.startswith("【会议记录】")
    ):
        return {
            "action": "click",
            "name": "点击会议记录-当前记录标题",
            "locator": {
                "by": "android_uiautomator",
                "value": 'new UiSelector().descriptionContains("【会议记录】")',
            },
            "timeout": 10,
            "click_mode": "center",
            "fallback_tap": {"x": 598, "y": 496},
            "wait_after": 1,
        }

    # 修改会议记录名称时，录制导出的第二个 instance(8) 是标题右侧的编辑入口。
    # 不能套用翻译页 instance(8)=底部麦克风的规则。
    if (
        "会议记录_修改名称" in title
        and 'className("android.view.View").instance(8)' in value
        and index >= 5
    ):
        return {
            "action": "tap_point",
            "name": "点击会议记录-名称编辑入口",
            "x": 928,
            "y": 463,
            "wait_after": 1,
        }

    # 首页 - 翻译中心
    if "翻译中心" in value:
        return {
            "action": "click",
            "name": "点击首页-翻译中心",
            "locator": {
                "by": "android_uiautomator",
                "value": 'new UiSelector().descriptionContains("翻译中心")',
            },
            "timeout": 30,
            "wait_after": 2
        }

    # 翻译模式页 - 开始双耳机模式
    if by == "accessibility_id" and value == "开始双耳机模式":
        return {
            "action": "click",
            "name": "点击翻译模式页-开始双耳机模式",
            "locator": {
                "by": "accessibility_id",
                "value": "开始双耳机模式",
            },
            "timeout": 30,
            "fallback_tap": {"x": 540, "y": 733},
            "wait_after": 1
        }

    # 翻译模式页 - 开始手机模式
    if by == "accessibility_id" and value == "开始手机模式":
        return {
            "action": "click",
            "name": "点击翻译模式页-开始手机模式",
            "locator": {
                "by": "accessibility_id",
                "value": "开始手机模式",
            },
            "timeout": 30,
            "fallback_tap": {"x": 540, "y": 735},
            "wait_after": 1
        }

    # 翻译模式页 - 开始单向模式
    if by == "accessibility_id" and value == "开始单向模式":
        return {
            "action": "click",
            "name": "点击翻译模式页-开始单向模式",
            "locator": {
                "by": "accessibility_id",
                "value": "开始单向模式",
            },
            "timeout": 30,
            "fallback_tap": {"x": 540, "y": 1298},
            "wait_after": 1
        }

    # 单向模式页底部麦克风按钮
    # Inspector 常见导出：
    # new UiSelector().className("android.view.View").instance(8)
    if 'className("android.view.View").instance(8)' in value:
        return {
            "action": "tap_point",
            "name": "点击底部麦克风按钮",
            "x": 540,
            "y": 2148,
            "wait_after": 1
        }

    # 双耳机/手机模式页底部麦克风按钮
    # Inspector 常见导出：
    # new UiSelector().className("android.view.View").instance(10)
    if 'className("android.view.View").instance(10)' in value:
        return {
            "action": "tap_point",
            "name": "点击底部麦克风按钮",
            "x": 540,
            "y": 2148,
            "wait_after": 1
        }

    # 睡眠中心音频卡片的完整 description 会随内容/加载状态变化，
    # 例如录制时是“放松心灵\n11 min”，回放时可能只剩“放松心灵”。
    if "睡眠中心_切换" in title and 'description("放松心灵\\n11 min")' in value:
        return description_contains_click_step(
            "放松心灵",
            "点击睡眠中心-当前音频卡片",
            title,
            fallback_tap={"x": 540, "y": 760},
        )

    if "睡眠中心_切换" in title and 'description("喜好意识\\n10 min")' in value:
        return description_contains_click_step(
            "喜好意识",
            "点击睡眠中心-当前音频卡片",
            title,
            fallback_tap={"x": 540, "y": 760},
        )

    # 日期选择器里的完整 accessibility id 带年份、月份、星期，当前生日状态一变就会漂。
    # 这里保留录制意图：切到上个月后选择 18 号。
    if (
        "修改生日" in title
        and by == "accessibility_id"
        and re.match(r"18,\s*\d{4}年\d{1,2}月18日", value)
    ):
        return description_contains_click_step(
            "18,",
            "点击生日日期：18号",
            title,
            fallback_tap={"x": 162, "y": 1502},
        )

    # 操作指引内容会随版本增减，录制里的“下一步”次数不能作为硬断言。
    if "操作指引" in title and by == "accessibility_id" and value == "下一步":
        return {
            "action": "click",
            "name": "点击操作指引-下一步（可选）",
            "locator": {
                "by": "accessibility_id",
                "value": "下一步",
            },
            "timeout": 3,
            "optional": True,
            "wait_after": 1,
        }

    if "操作指引" in title and by == "accessibility_id" and value == "跳过":
        return {
            "action": "click",
            "name": "结束操作指引",
            "locator": {
                "by": "accessibility_id",
                "value": "跳过",
            },
            "fallback_locators": [
                {
                    "by": "accessibility_id",
                    "value": "完成",
                }
            ],
            "timeout": 3,
            "fallback_timeout": 3,
            "optional": True,
            "wait_after": 1,
        }

    return None


def parse_swipe_events(text: str):
    swipe_pattern = re.compile(
        r"(?P<var>\w+)\.w3c_actions\.pointer_action\.move_to_location\(\s*"
        r"(?P<start_x>-?\d+)\s*,\s*(?P<start_y>-?\d+)\s*\)"
        r"(?:(?!(?P=var)\.perform\(\)).)*?"
        r"(?P=var)\.w3c_actions\.pointer_action\.pointer_down\(\)"
        r"(?:(?!(?P=var)\.perform\(\)).)*?"
        r"(?P=var)\.w3c_actions\.pointer_action\.move_to_location\(\s*"
        r"(?P<end_x>-?\d+)\s*,\s*(?P<end_y>-?\d+)\s*\)"
        r"(?:(?!(?P=var)\.perform\(\)).)*?"
        r"(?P=var)\.w3c_actions\.pointer_action\.(?:release|pointer_up)\(\)"
        r"(?:(?!(?P=var)\.perform\(\)).)*?"
        r"(?P=var)\.perform\(\)",
        re.S,
    )

    events = []
    for match in swipe_pattern.finditer(text):
        start_x = int(match.group("start_x"))
        start_y = int(match.group("start_y"))
        end_x = int(match.group("end_x"))
        end_y = int(match.group("end_y"))
        events.append({
            "position": match.start(),
            "step": {
                "action": "swipe_point",
                "name": step_name_from_swipe(start_x, start_y, end_x, end_y),
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
                "wait_after": 1,
            },
        })
    return events


def parse_tap_events(text: str):
    tap_pattern = re.compile(
        r"(?P<var>\w+)\.w3c_actions\.pointer_action\.move_to_location\(\s*"
        r"(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*\)"
        r"(?:(?!(?P=var)\.perform\(\)|(?P=var)\.w3c_actions\.pointer_action\.move_to_location\().)*?"
        r"(?P=var)\.w3c_actions\.pointer_action\.pointer_down\(\)"
        r"(?:(?!(?P=var)\.perform\(\)|(?P=var)\.w3c_actions\.pointer_action\.move_to_location\().)*?"
        r"(?P=var)\.w3c_actions\.pointer_action\.(?:release|pointer_up)\(\)"
        r"(?:(?!(?P=var)\.perform\(\)).)*?"
        r"(?P=var)\.perform\(\)",
        re.S,
    )

    events = []
    for match in tap_pattern.finditer(text):
        x = int(match.group("x"))
        y = int(match.group("y"))
        events.append({
            "position": match.start(),
            "step": {
                "action": "tap_point",
                "name": step_name_from_tap(x, y),
                "x": x,
                "y": y,
                "wait_after": 1,
            },
        })
    return events


def parse_inspector_python(py_file: Path):
    """
    解析 Appium Inspector 导出的 Python 代码。

    支持格式：
        el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="Auro AI")
        el1.click()

        el2 = driver.find_element(
            by=AppiumBy.ANDROID_UIAUTOMATOR,
            value="new UiSelector().description(\"翻译中心\\n面对面，无缝交流\")"
        )
        el2.click()
    """
    text = py_file.read_text(encoding="utf-8")
    title = py_file.stem if hasattr(py_file, "stem") else ""

    elements = {}
    clear_events = []

    find_pattern = re.compile(
        r"(?P<var>\w+)\s*=\s*driver\.find_element\(\s*"
        r"by\s*=\s*AppiumBy\.(?P<by>\w+)\s*,\s*"
        r"value\s*=\s*(?P<value>(?:\"(?:\\.|[^\"])*\")|(?:'(?:\\.|[^'])*'))\s*"
        r"\)",
        re.S,
    )

    for m in find_pattern.finditer(text):
        var = m.group("var")
        by = map_by(m.group("by"))
        value = unquote_python_string(m.group("value"))
        elements[var] = {
            "by": by,
            "value": value
        }

    clear_pattern = re.compile(r"(?P<var>\w+)\.clear\(\)")
    for m in clear_pattern.finditer(text):
        var = m.group("var")
        if var not in elements:
            continue
        clear_events.append({
            "position": m.start(),
            "locator": elements[var],
        })

    events = []
    raw_locators_in_click_order = []

    click_pattern = re.compile(r"(?P<var>\w+)\.click\(\)")

    for m in click_pattern.finditer(text):
        var = m.group("var")
        if var not in elements:
            continue

        locator = elements[var]
        raw_locators_in_click_order.append(locator)

        step_index = len(events) + 1

        known_step = convert_known_locator_to_point(locator, step_index, title=title)
        if known_step:
            events.append({"position": m.start(), "step": known_step})
            continue

        events.append({
            "position": m.start(),
            "step": {
                "action": "click",
                "name": step_name_from_locator(locator, step_index),
                "locator": locator,
                "timeout": 30,
                "wait_after": wait_after_click(locator, title)
            },
        })

    send_keys_pattern = re.compile(
        r"(?P<var>\w+)\.send_keys\(\s*"
        r"(?P<value>(?:\"(?:\\.|[^\"])*\")|(?:'(?:\\.|[^'])*'))\s*"
        r"\)"
    )

    for m in send_keys_pattern.finditer(text):
        var = m.group("var")
        if var not in elements:
            continue

        locator = elements[var]
        input_text = unquote_python_string(m.group("value"))
        events.append({
            "position": m.start(),
            "step": {
                "action": "input_text",
                "name": f"输入文本：{input_text}",
                "locator": locator,
                "text": input_text,
                "clear": any(
                    event["position"] < m.start()
                    and event["locator"] == locator
                    for event in clear_events
                ),
                "timeout": 30,
                "wait_after": 1,
            },
        })

    events.extend(parse_tap_events(text))
    events.extend(parse_swipe_events(text))
    events.sort(key=lambda item: item["position"])
    steps = [item["step"] for item in events]
    for index, step in enumerate(steps[:-1]):
        next_step = steps[index + 1]
        if step.get("action") != "input_text":
            continue
        if next_step.get("action") != "click":
            continue
        next_locator = next_step.get("locator") or {}
        if (
            next_locator.get("by") == "accessibility_id"
            and next_locator.get("value") in ("确定", "提交")
        ):
            step["hide_keyboard_after"] = False

    return steps, raw_locators_in_click_order


def recording_starts_from_launcher(raw_locators):
    """
    判断录制文件是不是从桌面点击 Auro AI 开始。
    如果第一步就是 Auro AI，说明这个录制文件自己负责启动 App。
    否则说明录制文件是从 App 首页开始的，框架要先进入 App 首页。
    """
    if not raw_locators:
        return False

    first = raw_locators[0]
    return (
        first.get("by") == "accessibility_id"
        and first.get("value") == "Auro AI"
    )


def recording_to_case(py_file: Path):
    raw_title = py_file.stem
    labels = parse_case_labels(raw_title)
    title = labels["clean_title"] or raw_title
    case_id = safe_case_id(title)

    steps, raw_locators = parse_inspector_python(py_file)

    if not steps:
        raise AssertionError(f"没有从录制文件中解析到任何 click 步骤：{py_file}")

    steps, execution_meta = augment_recording_steps(
        raw_title,
        steps,
        enable_audio=labels["enable_audio"],
        enable_translation=labels["enable_translation"],
    )

    starts_from_launcher = recording_starts_from_launcher(raw_locators)

    return {
        "case_id": case_id,
        "name": title,
        "description": f"从 recordings 自动加载的录制用例：{title}",
        "source_recording": str(py_file),
        "recording_meta": {
            "raw_title": raw_title,
            "enable_audio": labels["enable_audio"],
            "enable_translation": labels["enable_translation"],
            **execution_meta,
        },
        "start": {
            "mode": "launcher" if starts_from_launcher else "app",
            "no_reset": True,
            "ensure_app_home": not starts_from_launcher
        },
        "blockers": blockers_for_recording_title(title),
        "steps": steps
    }


def recording_sort_key(path: Path):
    labels = parse_case_labels(path.stem)
    priority = labels.get("priority")
    priority_order = priority if priority is not None else 99
    return (priority_order, path.stem.casefold())


def recording_id(path: Path):
    labels = parse_case_labels(path.stem)
    priority = labels.get("priority")
    if priority is None:
        return path.stem
    title = labels["clean_title"] or path.stem
    return f"P{priority}-{title}"


def load_recording_files():
    single_file = os.environ.get("RECORDING_FILE")
    if single_file:
        return [Path(single_file)]

    if not RECORDINGS_DIR.exists():
        return []

    files = []
    for path in sorted(RECORDINGS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        if path.name.lower() == "readme.py":
            continue
        files.append(path)

    return sorted(files, key=recording_sort_key)


def save_prelude_debug(driver, name):
    artifact_root = os.environ.get("APPIUM_ARTIFACTS_ROOT")
    if artifact_root:
        artifact_dir = Path(artifact_root) / "_recording_prelude"
    else:
        artifact_dir = PROJECT_ROOT / "artifacts" / "_recording_prelude"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    try:
        driver.save_screenshot(str(artifact_dir / f"{name}.png"))
    except Exception:
        pass

    try:
        (artifact_dir / f"{name}.xml").write_text(
            driver.page_source,
            encoding="utf-8"
        )
    except Exception:
        pass


def restart_app_once_for_run(driver, start_mode="app"):
    """
    每次 pytest 运行只重启一次 App。

    目的：避免测试开始前 App 停留在上一次手工操作或上一次失败用例的页面。
    注意：不是每条用例都重启；后续用例仍然走 ensure_app_home 回首页。
    """
    global APP_RESTARTED_ONCE
    if APP_RESTARTED_ONCE:
        return

    print("\n[PRELUDE] 本次测试运行开始，重启 Auro AI App 一次")
    try:
        driver.terminate_app(APP_PACKAGE)
        time.sleep(1)
    except Exception as exc:
        print(f"[PRELUDE] terminate_app 忽略异常: {exc}")

    if start_mode == "launcher":
        driver.press_keycode(3)
        time.sleep(1)
    else:
        driver.activate_app(APP_PACKAGE)
        time.sleep(2)

    save_prelude_debug(driver, "00_restart_app_once")
    APP_RESTARTED_ONCE = True


def ensure_app_home(driver, timeout=20, max_back=6):
    """
    确保进入 App 首页。

    用于这种录制文件：
    - 用户录制时已经在 App 首页；
    - 录制文件第一步是“点击翻译中心”；
    - 自动执行时，框架需要先把 App 拉起来并回到首页。

    首页判断文本：翻译中心
    """
    def wait_home(label, wait_seconds):
        end_time = time.time() + wait_seconds
        while time.time() < end_time:
            source = driver.page_source
            if HOME_MARKER in source:
                save_prelude_debug(driver, label)
                return True
            time.sleep(1)
        return False

    driver.activate_app(APP_PACKAGE)
    time.sleep(2)
    save_prelude_debug(driver, "01_after_activate_app")

    if wait_home("02_home_found", timeout):
        return

    for i in range(max_back):
        driver.press_keycode(4)  # BACK
        time.sleep(1)
        if wait_home(f"03_home_found_after_back_{i + 1}", 1):
            return

    save_prelude_debug(driver, "98_home_not_found_after_back")

    # 只在回首页失败时兜底重启 App，避免一个失败用例把后续整批用例带崩。
    print("[PRELUDE] 返回键无法回到首页，重启 Auro AI App 后再次检查首页")
    try:
        driver.terminate_app(APP_PACKAGE)
        time.sleep(1)
    except Exception as exc:
        print(f"[PRELUDE] recovery terminate_app 忽略异常: {exc}")

    driver.activate_app(APP_PACKAGE)
    time.sleep(2)
    save_prelude_debug(driver, "99_after_recovery_restart")

    if wait_home("100_home_found_after_recovery_restart", timeout):
        return

    save_prelude_debug(driver, "101_home_not_found_after_recovery_restart")
    raise AssertionError(
        "录制用例执行前置失败：已经启动 App，但没有找到首页文本“翻译中心”。"
        "已尝试返回键和重启 App 兜底，请检查 App 是否被系统弹窗、分享页或外部页面挡住。"
    )


RECORDING_FILES = load_recording_files()


@pytest.mark.parametrize(
    "recording_file",
    RECORDING_FILES,
    ids=[recording_id(p) for p in RECORDING_FILES]
)
def test_run_recording(recording_file):
    case_data = recording_to_case(recording_file)

    start = case_data.get("start", {})
    start_mode = start.get("mode", "app")
    no_reset = start.get("no_reset", True)

    last_error = None
    for attempt in range(1, 3):
        driver = None
        try:
            driver = create_driver(start_mode=start_mode, no_reset=no_reset)
            restart_app_once_for_run(driver, start_mode=start_mode)

            if start.get("ensure_app_home", False):
                ensure_app_home(driver)

            runner = CaseRunner(driver, case_data)
            runner.run()
            return
        except Exception as exc:
            last_error = exc
            if attempt >= 2 or not is_transient_session_error(exc):
                raise
            print(f"[RECOVERY] transient Appium/session error, retry case once: {exc}")
            restart_appium_server("retry recording case after transient session error")
        finally:
            quit_driver_safely(driver)

    if last_error:
        raise last_error
