from tests.test_run_recordings import parse_inspector_python, parse_swipe_events


class FakeRecording:
    def __init__(self, text, stem="fake_recording"):
        self.text = text
        self.stem = stem

    def read_text(self, encoding="utf-8"):
        return self.text


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

    assert [step["action"] for step in steps] == ["tap_point", "swipe_point", "click"]
    assert steps[1]["name"] == "滑动：上滑 (514,1372) -> (520,766)"
    assert [locator["value"] for locator in raw_locators] == ["开始双耳机模式", "关闭"]


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


def test_profile_my_account_uses_conditional_coordinate_tap():
    recording = FakeRecording(
        """
el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="我的账户")
el1.click()
""",
        stem="切换头像",
    )

    steps, _raw_locators = parse_inspector_python(recording)

    assert steps[0]["action"] == "tap_my_account"
    assert steps[0]["x"] == 540
    assert steps[0]["y"] == 1273
    assert steps[0]["attempts"] == 4
