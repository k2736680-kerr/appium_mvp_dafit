import re
from copy import deepcopy

from core.config import AUDIO_SETTLE_SECONDS


CASE_TAG_AUDIO = "[音频]"
CASE_TAG_TRANSLATION = "[翻译]"
PRIORITY_TAG_PATTERN = re.compile(
    r"^\s*(?:\[(?P<bracket>[Pp][0-3])\]|(?P<prefix>[Pp][0-3])(?:[_\-\s]+))"
)
MIC_STEP_NAME = "点击底部麦克风按钮"
STOP_RECORDING_KEYWORDS = ("关闭录音", "停止录音", "结束录音", "关闭", "停止", "结束")
START_RECORDING_KEYWORDS = ("开始录音", "开始录制", "开始记录", "录音", "录制")
LONG_PRESS_KEYWORDS = ("长按", "按住", "松开结束", "按住说话")

LANGUAGE_NAME_TO_CODE = {
    "中文": "zh",
    "英文": "en",
    "日语": "ja",
    "日文": "ja",
    "韩语": "ko",
}


def strip_case_tags(title: str) -> str:
    clean_title = PRIORITY_TAG_PATTERN.sub("", title, count=1)
    for tag in (CASE_TAG_TRANSLATION, CASE_TAG_AUDIO):
        clean_title = clean_title.replace(tag, "")
    return clean_title.strip()


def parse_case_priority(title: str):
    match = PRIORITY_TAG_PATTERN.match(title)
    if not match:
        return None

    value = match.group("bracket") or match.group("prefix")
    return int(value[1])


def parse_case_labels(title: str) -> dict:
    enable_translation = CASE_TAG_TRANSLATION in title
    enable_audio = enable_translation or CASE_TAG_AUDIO in title
    clean_title = strip_case_tags(title)
    return {
        "raw_title": title,
        "clean_title": clean_title,
        "priority": parse_case_priority(title),
        "enable_audio": enable_audio,
        "enable_translation": enable_translation,
    }


def parse_language_direction(title: str):
    for source_name, source_code in LANGUAGE_NAME_TO_CODE.items():
        for target_name, target_code in LANGUAGE_NAME_TO_CODE.items():
            if f"{source_name}转{target_name}" in title:
                return source_code, target_code
    return None


def infer_audio_language(title: str, enable_translation: bool = False) -> str:
    direction = parse_language_direction(title)
    if enable_translation and direction:
        return direction[0]

    matched = []
    for language_name, language_code in LANGUAGE_NAME_TO_CODE.items():
        index = title.find(language_name)
        if index >= 0:
            matched.append((index, language_code))

    if matched:
        matched.sort(key=lambda item: item[0])
        return matched[0][1]

    return "en"


def is_mic_step(step: dict) -> bool:
    name = step.get("name", "")
    if "麦克风" in name:
        return True

    role = step.get("role", "")
    if "mic" in role.lower():
        return True

    return False


def step_text(step: dict) -> str:
    locator = step.get("locator", {})
    return " ".join([
        step.get("name", ""),
        locator.get("value", ""),
        step.get("role", ""),
    ])


def is_start_recording_step(step: dict) -> bool:
    text = step_text(step)
    if is_mic_step(step):
        return True
    return any(keyword in text for keyword in START_RECORDING_KEYWORDS)


def is_stop_recording_step(step: dict) -> bool:
    text = step_text(step)

    for keyword in STOP_RECORDING_KEYWORDS:
        if keyword in text:
            return True

    return False


def is_long_press_recording_case(title: str) -> bool:
    return any(keyword in title for keyword in LONG_PRESS_KEYWORDS)


def find_recording_boundary_steps(steps: list[dict]):
    start_index = None
    stop_index = None

    for index, step in enumerate(steps):
        if start_index is None and is_start_recording_step(step):
            start_index = index
            continue

        if start_index is None:
            continue

        if is_stop_recording_step(step):
            stop_index = index
            break

        if is_start_recording_step(step):
            stop_index = index
            break

    if start_index is None:
        for index, step in enumerate(steps):
            if is_stop_recording_step(step) and index > 0:
                start_index = index - 1
                stop_index = index
                break

    if start_index is not None and stop_index is None:
        for index in range(start_index + 1, len(steps)):
            step = steps[index]
            if is_stop_recording_step(step):
                stop_index = index
                break

        if stop_index is None and start_index + 1 < len(steps):
            stop_index = start_index + 1

    return start_index, stop_index


def build_audio_step(audio_lang: str) -> dict:
    return {
        "action": "play_audio",
        "name": f"播放测试音频({audio_lang})",
        "file": f"assets/audio/source_{audio_lang}.wav",
        "fallback_file": "assets/audio/source_en.wav",
        "wait_after": 0,
    }


def build_long_press_audio_step(audio_lang: str) -> dict:
    return {
        "action": "long_press_role",
        "name": f"长按底部麦克风并播放测试音频({audio_lang})",
        "role": "bottom_mic",
        "audio_file": f"assets/audio/source_{audio_lang}.wav",
        "fallback_file": "assets/audio/source_en.wav",
        "duration": "auto",
        "audio_start_delay_ms": 300,
        "release_padding_ms": 700,
        "wait_after": 1,
    }


def build_translation_assert_step(source_lang: str, target_lang: str) -> dict:
    return {
        "action": "assert_translation",
        "name": "校验翻译语义",
        "source_lang": source_lang,
        "target_lang": target_lang,
        "timeout": 45,
        "poll_interval": 2,
    }


def build_audio_settle_step() -> dict:
    return {
        "action": "sleep",
        "name": "等待录音收尾",
        "seconds": AUDIO_SETTLE_SECONDS,
        "check_blockers": False,
    }


def augment_recording_steps(title: str, steps: list[dict], enable_audio: bool, enable_translation: bool):
    steps = [deepcopy(step) for step in steps]
    metadata = {
        "audio_lang": None,
        "source_lang": None,
        "target_lang": None,
    }

    if not enable_audio:
        return steps, metadata

    use_long_press = is_long_press_recording_case(title)
    start_index, stop_index = find_recording_boundary_steps(steps)
    if start_index is None or (stop_index is None and not use_long_press):
        raise AssertionError(
            f"录制用例 {title} 启用了音频能力，但没有识别到完整的录音开始/结束动作。"
            "普通录音请在录制里保留开始和结束动作；长按录音请在文件名里包含“长按”。"
        )

    audio_lang = infer_audio_language(title, enable_translation=enable_translation)
    metadata["audio_lang"] = audio_lang

    direction = parse_language_direction(title)
    if enable_translation:
        if not direction:
            raise AssertionError(
                f"录制用例 {title} 启用了翻译校验，但文件名里没有识别到“中文转英文”这类语言方向。"
            )
        metadata["source_lang"], metadata["target_lang"] = direction

    augmented_steps = []

    for index, step in enumerate(steps):
        if enable_translation and index == start_index:
            augmented_steps.append({
                "action": "mark_page_texts",
                "name": "记录翻译前页面文本",
                "check_blockers": False,
            })

        if use_long_press and index == start_index:
            augmented_steps.append(build_long_press_audio_step(audio_lang))
            if AUDIO_SETTLE_SECONDS > 0:
                augmented_steps.append(build_audio_settle_step())
            if enable_translation and stop_index is None:
                augmented_steps.append(
                    build_translation_assert_step(
                        metadata["source_lang"],
                        metadata["target_lang"],
                    )
                )
            continue

        if use_long_press and stop_index is not None and index == stop_index:
            if enable_translation:
                augmented_steps.append(
                    build_translation_assert_step(
                        metadata["source_lang"],
                        metadata["target_lang"],
                    )
                )
            continue

        augmented_steps.append(step)

        if index == start_index:
            augmented_steps.append(build_audio_step(audio_lang))
            if AUDIO_SETTLE_SECONDS > 0:
                augmented_steps.append(build_audio_settle_step())

        if enable_translation and index == stop_index:
            augmented_steps.append(
                build_translation_assert_step(
                    metadata["source_lang"],
                    metadata["target_lang"],
                )
            )

    return augmented_steps, metadata
