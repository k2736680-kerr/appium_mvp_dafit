import pytest
from selenium.common.exceptions import NoSuchElementException

import core.runner as runner_module
from core.runner import CaseRunner
from tests.test_run_recordings import is_translation_timeout_error


class FakeMicElement:
    def __init__(self, rect):
        self.rect = rect


class FakeMicDriver:
    def __init__(self, mic_rect=None):
        self.mic_rect = mic_rect
        self.long_clicks = []

    @property
    def page_source(self):
        return "<hierarchy />"

    def find_element(self, _by, value):
        if value == "手机麦克风" and self.mic_rect is not None:
            return FakeMicElement(self.mic_rect)
        raise NoSuchElementException(value)

    def get_window_size(self):
        return {"width": 1080, "height": 2400}

    def execute_script(self, script, args=None):
        if script == "mobile: longClickGesture":
            self.long_clicks.append(args)


@pytest.fixture(autouse=True)
def fast_mic_resolve(monkeypatch):
    monkeypatch.setattr(runner_module, "MIC_POINT_RESOLVE_TIMEOUT", 0.1)
    monkeypatch.setattr(runner_module, "DEFAULT_TIMEOUT", 0.1)
    monkeypatch.setattr("core.runner.time.sleep", lambda _seconds: None)


def make_runner(driver):
    return CaseRunner(driver, {"case_id": "unit_mic"})


def test_resolve_bottom_mic_point_uses_element_center():
    driver = FakeMicDriver(mic_rect={"x": 478, "y": 1960, "width": 213, "height": 61})
    runner = make_runner(driver)

    assert runner.resolve_bottom_mic_point() == (584, 1990)


def test_resolve_bottom_mic_point_returns_none_when_missing():
    driver = FakeMicDriver(mic_rect=None)
    runner = make_runner(driver)

    assert runner.resolve_bottom_mic_point() is None


def test_long_press_role_prefers_dynamic_mic_point():
    driver = FakeMicDriver(mic_rect={"x": 478, "y": 1960, "width": 213, "height": 61})
    runner = make_runner(driver)

    runner.action_long_press_role({"role": "bottom_mic"})

    assert len(driver.long_clicks) == 1
    assert driver.long_clicks[0]["x"] == 584
    assert driver.long_clicks[0]["y"] == 1990


def test_long_press_role_falls_back_to_ratio_point():
    driver = FakeMicDriver(mic_rect=None)
    runner = make_runner(driver)

    runner.action_long_press_role({"role": "bottom_mic"})

    assert len(driver.long_clicks) == 1
    assert driver.long_clicks[0]["x"] == 540
    assert driver.long_clicks[0]["y"] == 2148


def test_long_press_role_rejects_unknown_role():
    runner = make_runner(FakeMicDriver())

    with pytest.raises(ValueError):
        runner.action_long_press_role({"role": "nonexistent"})


def test_translation_timeout_error_detection():
    silent = AssertionError(
        "等待翻译结果超时：source_lang=zh, target_lang=en, "
        "last_source=None, last_target=None, visible_texts=[...]"
    )
    semantic = AssertionError(
        "等待翻译结果超时：source_lang=zh, target_lang=en, "
        "last_source=你好, last_target=Hello, visible_texts=[...]. "
        "最后一次语义校验：..."
    )

    assert is_translation_timeout_error(silent) is True
    assert is_translation_timeout_error(semantic) is False
    assert is_translation_timeout_error(ValueError("等待翻译结果超时")) is False
    assert is_translation_timeout_error(AssertionError("其他断言")) is False
