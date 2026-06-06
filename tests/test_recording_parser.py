from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_run_recordings import (
    parse_inspector_python,
    parse_swipe_events,
    parse_tap_events,
    recording_to_case,
    recording_id,
    recording_sort_key,
)
from core.runner import CaseRunner


class FakeRecording:
    def __init__(self, text, stem="fake_recording"):
        self.text = text
        self.stem = stem

    def read_text(self, encoding="utf-8"):
        return self.text


class FakeDriver:
    def __init__(self, page_source):
        self.page_source = page_source

    def save_screenshot(self, _path):
        return True

    def get_screenshot_as_base64(self):
        return "fake-screenshot-base64"


def test_parse_swipe_events_from_action_chains():
    text = """
actions = ActionChains(driver)
actions.w3c_actions = ActionBuilder(driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch"))
actions.w3c_actions.pointer_action.move_to_location(514, 1372)
actions.w3c_actions.pointer_action.pointer_down()
actions.w3c_actions.pointer_action.move_to_location(520, 766)
actions.w3c_actions.pointer_action.release()
actions.perform()
"""

    events = parse_swipe_events(text)

    assert len(events) == 1
    assert events[0]["step"] == {
        "action": "swipe_point",
        "name": "滑动：上滑 (514,1372) -> (520,766)",
        "start_x": 514,
        "start_y": 1372,
        "end_x": 520,
        "end_y": 766,
        "wait_after": 1,
    }


def test_parse_tap_events_from_action_chains():
    text = """
actions = ActionChains(driver)
actions.w3c_actions = ActionBuilder(driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch"))
actions.w3c_actions.pointer_action.move_to_location(497, 444)
actions.w3c_actions.pointer_action.pointer_down()
actions.w3c_actions.pointer_action.pause(0.1)
actions.w3c_actions.pointer_action.release()
actions.perform()
"""

    events = parse_tap_events(text)

    assert len(events) == 1
    assert events[0]["step"] == {
        "action": "tap_point",
        "name": "点击坐标：(497,444)",
        "x": 497,
        "y": 444,
        "wait_after": 1,
    }


def test_parse_inspector_python_keeps_click_and_swipe_order():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="开始双耳机模式")
el1.click()

actions = ActionChains(driver)
actions.w3c_actions = ActionBuilder(driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch"))
actions.w3c_actions.pointer_action.move_to_location(514, 1372)
actions.w3c_actions.pointer_action.pointer_down()
actions.w3c_actions.pointer_action.move_to_location(520, 766)
actions.w3c_actions.pointer_action.release()
actions.perform()

el2 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="关闭")
el2.click()
""",
    )

    steps, raw_locators = parse_inspector_python(recording)

    assert [step["action"] for step in steps] == ["click", "swipe_point", "click"]
    assert steps[0]["locator"] == {"by": "accessibility_id", "value": "开始双耳机模式"}
    assert steps[0]["fallback_tap"] == {"x": 540, "y": 733}
    assert steps[1]["name"] == "滑动：上滑 (514,1372) -> (520,766)"
    assert [locator["value"] for locator in raw_locators] == ["开始双耳机模式", "关闭"]


def test_parse_phone_mode_entry_prefers_text_with_coordinate_fallback():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="开始手机模式")
el1.click()
""",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["action"] == "click"
    assert steps[0]["locator"] == {"by": "accessibility_id", "value": "开始手机模式"}
    assert steps[0]["fallback_tap"] == {"x": 540, "y": 735}


def test_parse_single_mode_entry_prefers_text_with_coordinate_fallback():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="开始单向模式")
el1.click()
""",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["action"] == "click"
    assert steps[0]["locator"] == {"by": "accessibility_id", "value": "开始单向模式"}
    assert steps[0]["fallback_tap"] == {"x": 540, "y": 1298}


def test_parse_inspector_python_keeps_click_and_tap_order():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="历史")
el1.click()

actions = ActionChains(driver)
actions.w3c_actions = ActionBuilder(driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch"))
actions.w3c_actions.pointer_action.move_to_location(472, 592)
actions.w3c_actions.pointer_action.pointer_down()
actions.w3c_actions.pointer_action.pause(0.1)
actions.w3c_actions.pointer_action.release()
actions.perform()

el2 = driver.find_element(by=AppiumBy.CLASS_NAME, value="android.widget.Button")
el2.click()
""",
    )

    steps, raw_locators = parse_inspector_python(recording)

    assert [step["action"] for step in steps] == ["click", "tap_point", "click"]
    assert steps[1]["name"] == "点击坐标：(472,592)"
    assert [locator["value"] for locator in raw_locators] == ["历史", "android.widget.Button"]


def test_parse_single_direction_mode_instance_8_as_bottom_mic():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="开始单向模式")
el1.click()
el2 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.view.View\\").instance(8)")
el2.click()
el3 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.view.View\\").instance(8)")
el3.click()
"""
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert [step["action"] for step in steps] == ["click", "tap_point", "tap_point"]
    assert steps[1]["name"] == "点击底部麦克风按钮"
    assert steps[1]["x"] == 540
    assert steps[1]["y"] == 2148


def test_parse_meeting_record_instance_7_as_record_button():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"会议记录\\\\n转录和总结\\")")
el1.click()
el2 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.view.View\\").instance(7)")
el2.click()
el3 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="结束录制")
el3.click()
""",
        stem="[音频]会议记录",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert [step["action"] for step in steps] == ["click", "tap_point", "click"]
    assert steps[1]["name"] == "点击会议记录-底部录制按钮"
    assert steps[1]["x"] == 540
    assert steps[1]["y"] == 2169


def test_parse_send_keys_as_input_text():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="AI 助手")
el1.click()
el2 = driver.find_element(by=AppiumBy.CLASS_NAME, value="android.widget.EditText")
el2.click()
el2.send_keys("你好，今天天气怎么样")
el3 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.widget.Button\\").instance(1)")
el3.click()
""",
        stem="AI助手_输入框",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert [step["action"] for step in steps] == ["click", "click", "input_text", "click"]
    assert steps[2]["name"] == "输入文本：你好，今天天气怎么样"
    assert steps[2]["locator"] == {"by": "class_name", "value": "android.widget.EditText"}
    assert steps[2]["text"] == "你好，今天天气怎么样"


def test_parse_profile_fields_ignore_recorded_values():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"生日\\\\n2001-04-19\\")")
el1.click()
el2 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"用户名\\\\n12313321321\\")")
el2.click()
el3 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"性别\\\\n男\\")")
el3.click()
""",
        stem="修改个人资料",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert [step["locator"]["value"] for step in steps] == [
        'new UiSelector().descriptionContains("生日")',
        'new UiSelector().descriptionContains("用户名")',
        'new UiSelector().descriptionContains("性别")',
    ]


def test_parse_avatar_image_as_stable_coordinate_for_avatar_case():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.CLASS_NAME, value="android.widget.ImageView")
el1.click()
""",
        stem="切换头像",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps == [
        {
            "action": "tap_point",
            "name": "点击我的账户-头像",
            "x": 540,
            "y": 489,
            "wait_after": 2,
        }
    ]


def test_profile_submit_waits_for_save_state_to_clear():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="提交")
el1.click()
""",
        stem="切换性别",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["wait_after"] == 4


def test_profile_my_account_uses_dynamic_account_entry_tap():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="我的账户")
el1.click()
""",
        stem="切换头像",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["action"] == "tap_my_account"
    assert "x" not in steps[0]
    assert "y" not in steps[0]
    assert steps[0]["attempts"] == 6
    assert steps[0]["attempt_wait"] == 1.5


def test_language_description_is_weakened_to_name_contains():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"日本語\\\\nJapanese\\")")
el1.click()
""",
        stem="语音记录_切换语言",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["locator"] == {
        "by": "android_uiautomator",
        "value": 'new UiSelector().descriptionContains("日本語")',
    }


def test_setting_value_description_is_weakened_to_field_name():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"语音播报语速\\\\n1.20x\\")")
el1.click()
""",
        stem="语音播报速度切换",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["locator"] == {
        "by": "android_uiautomator",
        "value": 'new UiSelector().descriptionContains("语音播报语速")',
    }


def test_sleep_center_card_prefers_text_with_coordinate_fallback():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"放松心灵\\\\n11 min\\")")
el1.click()
""",
        stem="睡眠中心_切换",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["action"] == "click"
    assert steps[0]["locator"] == {
        "by": "android_uiautomator",
        "value": 'new UiSelector().descriptionContains("放松心灵")',
    }
    assert steps[0]["fallback_tap"] == {"x": 540, "y": 760}


def test_meeting_rename_record_title_uses_dynamic_title_prefix():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="【会议记录】05-28 15:51")
el1.click()
""",
        stem="会议记录_修改名称",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["name"] == "点击会议记录-当前记录标题"
    assert steps[0]["locator"] == {
        "by": "android_uiautomator",
        "value": 'new UiSelector().descriptionContains("【会议记录】")',
    }
    assert steps[0]["click_mode"] == "center"
    assert steps[0]["fallback_tap"] == {"x": 598, "y": 496}


def test_meeting_rename_second_instance_8_is_edit_entry_not_bottom_mic():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="A")
el1.click()
el2 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="B")
el2.click()
el3 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="C")
el3.click()
el4 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="D")
el4.click()
el5 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.view.View\\").instance(8)")
el5.click()
""",
        stem="会议记录_修改名称",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[4]["name"] == "点击会议记录-名称编辑入口"
    assert steps[4]["x"] == 928
    assert steps[4]["y"] == 463


def test_birthday_date_picker_selects_day_without_fixed_month():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="18, 2002年9月18日星期三")
el1.click()
""",
        stem="修改生日",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["name"] == "点击生日日期：18号"
    assert steps[0]["locator"] == {
        "by": "android_uiautomator",
        "value": 'new UiSelector().descriptionContains("18,")',
    }
    assert steps[0]["fallback_tap"] == {"x": 162, "y": 1502}


def test_operation_guide_next_is_optional_and_finish_can_replace_skip():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="下一步")
el1.click()
el2 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="跳过")
el2.click()
""",
        stem="操作指引",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["optional"] is True
    assert steps[0]["timeout"] == 3
    assert steps[1]["name"] == "结束操作指引"
    assert steps[1]["fallback_locators"] == [
        {"by": "accessibility_id", "value": "完成"}
    ]


def test_recording_priority_sort_key_orders_p0_to_p3_before_unmarked():
    files = [
        Path("recordings/普通用例.py"),
        Path("recordings/[P2]第二批.py"),
        Path("recordings/p0_冒烟.py"),
        Path("recordings/[P1][翻译]核心翻译_中文转英文.py"),
        Path("recordings/[P3]低优先级.py"),
    ]

    ordered = sorted(files, key=recording_sort_key)

    assert [recording_id(path) for path in ordered] == [
        "P0-冒烟",
        "P1-核心翻译_中文转英文",
        "P2-第二批",
        "P3-低优先级",
        "普通用例",
    ]


def test_recording_case_uses_shared_error_blockers():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"图片翻译\\\\n拍摄并翻译\\")")
el1.click()
el2 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.view.View\\").instance(6)")
el2.click()
""",
        stem="[P0]图片翻译_英文拍照",
    )

    case_data = recording_to_case(recording)

    assert "图片翻译失败" in case_data["blockers"]
    assert "请重试" not in case_data["blockers"]
    assert case_data["recording_meta"]["image_translation"] is False
    assert case_data["recording_meta"]["source_lang"] is None
    assert case_data["recording_meta"]["target_lang"] is None
    assert [step["action"] for step in case_data["steps"]] == ["click", "click"]


def test_recording_album_image_translation_appends_visual_assert():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"图片翻译\\\\n拍摄并翻译\\")")
el1.click()
el2 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.view.View\\").instance(7)")
el2.click()
""",
        stem="[P0]图片翻译_英文拍照_相册",
    )

    case_data = recording_to_case(recording)

    assert case_data["recording_meta"]["image_translation"] is True
    assert case_data["recording_meta"]["source_lang"] == "en"
    assert case_data["recording_meta"]["target_lang"] == "zh"
    assert case_data["steps"][-1] == {
        "action": "assert_image_translation",
        "name": "校验图片翻译结果",
        "wait_before": 5,
        "timeout": 60,
        "poll_interval": 6,
        "check_blockers": False,
        "source_lang": "en",
        "target_lang": "zh",
    }


def test_parse_album_photo_picker_uses_semantic_thumbnail_action():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"图片翻译\\\\n拍摄并翻译\\")")
el1.click()
el2 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.view.View\\").instance(24)")
el2.click()
""",
        stem="[P0]图片翻译_英文拍照_相册",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[1] == {
        "action": "tap_first_album_photo",
        "name": "点击 Google 相册第一张图片",
        "description_contains": ["照片拍摄于", "Photo taken"],
        "fallback_tap": {"x": 179, "y": 1354},
        "timeout": 15,
        "wait_after": 1,
    }


def test_parse_album_done_button_uses_text_center_action():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\\"图片翻译\\\\n拍摄并翻译\\")")
el1.click()
el2 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().className(\\"android.widget.Button\\").instance(6)")
el2.click()
""",
        stem="[P0]图片翻译_英文拍照_相册",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[1] == {
        "action": "tap_text_center",
        "name": "点击 Google 相册完成",
        "texts": ["完成", "Done"],
        "fallback_tap": {"x": 913, "y": 2202},
        "timeout": 15,
        "wait_after": 2,
    }


def test_assert_translation_fails_on_blocker_before_stale_text_validation(monkeypatch):
    page_source = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy index="0" class="hierarchy">
  <android.view.View index="0" text="Hello, nice to meet you." content-desc="" />
  <android.view.View index="1" text="你好，很高兴见到你。" content-desc="" />
  <android.view.View index="2" text="图片翻译失败，请重试" content-desc="" />
</hierarchy>
"""
    with TemporaryDirectory() as artifacts_root:
        monkeypatch.setenv("APPIUM_ARTIFACTS_ROOT", artifacts_root)
        runner = CaseRunner(FakeDriver(page_source), {"case_id": "unit_blocker"})

        class FailIfCalledValidator:
            def validate_translation(self, **_kwargs):
                raise AssertionError("不应在阻断错误出现后继续做语义校验")

        runner.translation = FailIfCalledValidator()

        try:
            runner.action_assert_translation({
                "source_lang": "en",
                "target_lang": "zh",
                "timeout": 1,
            })
        except AssertionError as exc:
            assert "阻断信息：图片翻译失败" in str(exc)
        else:
            raise AssertionError("expected translation assertion to fail on blocker")


def test_assert_image_translation_uses_visual_validator(monkeypatch):
    page_source = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy index="0" class="hierarchy">
  <android.view.View index="0" text="图片翻译" content-desc="" />
</hierarchy>
"""
    calls = []

    with TemporaryDirectory() as artifacts_root:
        monkeypatch.setenv("APPIUM_ARTIFACTS_ROOT", artifacts_root)
        runner = CaseRunner(FakeDriver(page_source), {"case_id": "unit_image_translation"})

        class FakeImageValidator:
            def validate_image_translation(self, **kwargs):
                calls.append(kwargs)
                return {
                    "pass": True,
                    "reason": "",
                    "source_text": "Open",
                    "translated_text": "打开",
                    "evidence": "截图中展示了源文字和中文译文",
                }

        runner.image_translation = FakeImageValidator()

        runner.action_assert_image_translation({
            "source_lang": "en",
            "target_lang": "zh",
            "wait_before": 0,
        })

    assert calls == [{
        "screenshot_base64": "fake-screenshot-base64",
        "source_lang": "en",
        "target_lang": "zh",
    }]


def test_assert_image_translation_retries_until_result_loaded(monkeypatch):
    page_source = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy index="0" class="hierarchy">
  <android.view.View index="0" text="图片翻译" content-desc="" />
</hierarchy>
"""
    with TemporaryDirectory() as artifacts_root:
        monkeypatch.setenv("APPIUM_ARTIFACTS_ROOT", artifacts_root)
        runner = CaseRunner(FakeDriver(page_source), {"case_id": "unit_image_translation_retry"})

        class FakeImageValidator:
            def __init__(self):
                self.calls = 0

            def validate_image_translation(self, **_kwargs):
                self.calls += 1
                return {
                    "pass": self.calls == 2,
                    "reason": "仍在加载中" if self.calls == 1 else "",
                    "source_text": "" if self.calls == 1 else "Open",
                    "translated_text": "" if self.calls == 1 else "打开",
                    "evidence": "转圈中" if self.calls == 1 else "截图中展示了译文",
                }

        validator = FakeImageValidator()
        runner.image_translation = validator

        runner.action_assert_image_translation({
            "source_lang": "en",
            "target_lang": "zh",
            "wait_before": 0,
            "timeout": 1,
            "poll_interval": 0,
        })

    assert validator.calls == 2
