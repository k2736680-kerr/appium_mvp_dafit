from core.recording_rules import (
    augment_recording_steps,
    infer_audio_language,
    parse_case_labels,
    parse_language_direction,
)


def test_parse_case_labels_translation_implies_audio():
    labels = parse_case_labels("[翻译]双耳机模式_中文转英文")
    assert labels["clean_title"] == "双耳机模式_中文转英文"
    assert labels["enable_audio"] is True
    assert labels["enable_translation"] is True


def test_parse_language_direction():
    assert parse_language_direction("双耳机模式_中文转英文") == ("zh", "en")


def test_infer_audio_language_audio_only_uses_first_language():
    assert infer_audio_language("[音频]会议记录_中文") == "zh"
    assert infer_audio_language("[音频]语音记录_英文") == "en"


def test_augment_recording_steps_for_translation():
    steps = [
        {"action": "tap_point", "name": "点击首页-翻译中心"},
        {"action": "tap_point", "name": "点击底部麦克风按钮"},
        {"action": "tap_point", "name": "点击底部麦克风按钮"},
    ]

    augmented_steps, metadata = augment_recording_steps(
        "[翻译]双耳机模式_中文转英文",
        steps,
        enable_audio=True,
        enable_translation=True,
    )

    actions = [step["action"] for step in augmented_steps]
    assert actions == [
        "tap_point",
        "mark_page_texts",
        "tap_point",
        "play_audio",
        "sleep",
        "tap_point",
        "assert_translation",
    ]
    assert metadata["audio_lang"] == "zh"
    assert metadata["source_lang"] == "zh"
    assert metadata["target_lang"] == "en"


def test_augment_recording_steps_accepts_explicit_close_action():
    steps = [
        {"action": "tap_point", "name": "点击底部麦克风按钮"},
        {"action": "click", "name": "点击关闭录音", "locator": {"by": "accessibility_id", "value": "关闭录音"}},
    ]

    augmented_steps, metadata = augment_recording_steps(
        "[音频]会议记录_中文",
        steps,
        enable_audio=True,
        enable_translation=False,
    )

    assert [step["action"] for step in augmented_steps] == [
        "tap_point",
        "play_audio",
        "sleep",
        "click",
    ]
    assert metadata["audio_lang"] == "zh"


def test_augment_recording_steps_requires_start_and_stop_actions():
    steps = [{"action": "tap_point", "name": "点击底部麦克风按钮"}]

    try:
        augment_recording_steps(
            "[音频]会议记录_中文",
            steps,
            enable_audio=True,
            enable_translation=False,
        )
    except AssertionError as exc:
        assert "完整的录音开始/结束动作" in str(exc)
    else:
        raise AssertionError("expected audio-tagged recording to require two mic steps")


def test_augment_recording_steps_long_press_uses_single_synced_action():
    steps = [
        {"action": "tap_point", "name": "点击首页-翻译中心"},
        {"action": "tap_point", "name": "点击底部麦克风按钮"},
    ]

    augmented_steps, metadata = augment_recording_steps(
        "[翻译]手机耳机模式_长按录音_英文转中文",
        steps,
        enable_audio=True,
        enable_translation=True,
    )

    assert [step["action"] for step in augmented_steps] == [
        "tap_point",
        "mark_page_texts",
        "long_press_role",
        "sleep",
        "assert_translation",
    ]
    long_press_step = augmented_steps[2]
    assert long_press_step["role"] == "bottom_mic"
    assert long_press_step["audio_file"] == "assets/audio/source_en.wav"
    assert long_press_step["duration"] == "auto"
    assert metadata["audio_lang"] == "en"


def test_augment_recording_steps_long_press_skips_recorded_stop_click():
    steps = [
        {"action": "tap_point", "name": "点击底部麦克风按钮"},
        {"action": "tap_point", "name": "点击底部麦克风按钮"},
    ]

    augmented_steps, _metadata = augment_recording_steps(
        "[音频]会议记录_长按录音_中文",
        steps,
        enable_audio=True,
        enable_translation=False,
    )

    assert [step["action"] for step in augmented_steps] == [
        "long_press_role",
        "sleep",
    ]


def test_augment_recording_steps_infers_start_before_explicit_stop():
    steps = [
        {"action": "click", "name": "点击会议记录入口"},
        {"action": "click", "name": "点击录制元素 instance：第 2 步"},
        {"action": "click", "name": "点击：结束录制", "locator": {"by": "accessibility_id", "value": "结束录制"}},
    ]

    augmented_steps, metadata = augment_recording_steps(
        "[音频]会议记录",
        steps,
        enable_audio=True,
        enable_translation=False,
    )

    assert [step["action"] for step in augmented_steps] == [
        "click",
        "click",
        "play_audio",
        "sleep",
        "click",
    ]
    assert metadata["audio_lang"] == "en"


def test_augment_recording_steps_accepts_start_recording_text_and_next_stop():
    steps = [
        {"action": "click", "name": "点击语音记录入口"},
        {"action": "click", "name": "点击：开始录音", "locator": {"by": "accessibility_id", "value": "开始录音"}},
        {"action": "click", "name": "点击录制元素 instance：第 3 步"},
    ]

    augmented_steps, metadata = augment_recording_steps(
        "[音频]语音记录",
        steps,
        enable_audio=True,
        enable_translation=False,
    )

    assert [step["action"] for step in augmented_steps] == [
        "click",
        "click",
        "play_audio",
        "sleep",
        "click",
    ]
    assert metadata["audio_lang"] == "en"
