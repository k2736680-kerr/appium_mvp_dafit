import pytest
from selenium.common.exceptions import NoSuchElementException

from core.runner import CaseRunner


class FakeElement:
    def __init__(self, driver, name, on_click=None):
        self.driver = driver
        self.name = name
        self.on_click = on_click
        self.rect = {"x": 0, "y": 0, "width": 100, "height": 100}
        self.text = ""

    def click(self):
        if self.on_click:
            self.on_click()

    def clear(self):
        self.text = ""

    def send_keys(self, value):
        self.text += value
        if self.name == "account":
            self.driver.account = self.text
        if self.name == "password":
            self.driver.password = self.text

    def get_attribute(self, name):
        if name == "checked":
            return "true" if self.driver.checkbox_checked else "false"
        return ""

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


class FakeLoginDriver:
    def __init__(self, state="login_required", login_success=True):
        self.state = state
        self.login_success = login_success
        self.account = ""
        self.password = ""
        self.checkbox_checked = False
        self.clicks = []
        self.keycodes = []

    @property
    def page_source(self):
        pages = {
            "login_required": (
                '<hierarchy><node content-desc="需要登录" />'
                '<node content-desc="请登录以编辑您的个人资料。" />'
                '<node content-desc="点击登录" /></hierarchy>'
            ),
            "login_entry": '<hierarchy><node content-desc="点击登录" /></hierarchy>',
            "login_form": (
                '<hierarchy><android.widget.EditText />'
                '<android.widget.EditText /><android.widget.CheckBox />'
                '<node content-desc="登录" /></hierarchy>'
            ),
            "my_page": '<hierarchy><node content-desc="我的账户" /></hierarchy>',
            "account_page": (
                '<hierarchy><node content-desc="用户名" />'
                '<node content-desc="生日" /><node content-desc="性别" /></hierarchy>'
            ),
        }
        return pages[self.state]

    def find_element(self, _by, value):
        if "点击登录" in value and self.state in {"login_required", "login_entry"}:
            return FakeElement(self, "login_entry", self.open_login_form)
        if 'EditText").instance(0)' in value and self.state == "login_form":
            return FakeElement(self, "account")
        if 'EditText").instance(1)' in value and self.state == "login_form":
            return FakeElement(self, "password")
        if value == "android.widget.CheckBox" and self.state == "login_form":
            return FakeElement(self, "checkbox", self.check_agreement)
        if "登录" in value and self.state == "login_form":
            return FakeElement(self, "login_button", self.submit_login)
        if "我的账户" in value and self.state == "my_page":
            return FakeElement(self, "my_account", self.open_account_page)
        raise NoSuchElementException(value)

    def execute_script(self, name, payload):
        self.clicks.append((name, payload))
        if self.state == "my_page" and name == "mobile: clickGesture":
            self.open_account_page()

    def press_keycode(self, keycode):
        self.keycodes.append(keycode)
        if self.state == "login_required":
            self.state = "login_entry"

    def save_screenshot(self, _path):
        return True

    def get_screenshot_as_base64(self):
        return "fake-screenshot-base64"

    def open_login_form(self):
        self.state = "login_form"

    def check_agreement(self):
        self.checkbox_checked = True

    def submit_login(self):
        if self.login_success and self.account and self.password and self.checkbox_checked:
            self.state = "my_page"

    def open_account_page(self):
        self.state = "account_page"


@pytest.fixture(autouse=True)
def fast_runner_sleep(monkeypatch):
    monkeypatch.setattr("core.runner.time.sleep", lambda _seconds: None)


def make_runner(monkeypatch, tmp_path, driver, account="user@example.com", password="secret"):
    monkeypatch.setattr("core.runner.AURO_AUTO_LOGIN", True)
    monkeypatch.setattr("core.runner.AURO_LOGIN_ACCOUNT", account)
    monkeypatch.setattr("core.runner.AURO_LOGIN_PASSWORD", password)
    monkeypatch.setattr("core.runner.DEFAULT_TIMEOUT", 0.1)
    monkeypatch.setenv("APPIUM_ARTIFACTS_ROOT", str(tmp_path))
    return CaseRunner(driver, {"case_id": "unit_login"})


def test_tap_my_account_auto_logs_in_and_retries(monkeypatch, tmp_path):
    driver = FakeLoginDriver(state="login_required")
    runner = make_runner(monkeypatch, tmp_path, driver)

    runner.action_tap_my_account({
        "action": "tap_my_account",
        "attempts": 2,
        "attempt_wait": 0,
        "wait_after": 0,
    })

    assert driver.state == "account_page"
    assert driver.account == "user@example.com"
    assert driver.password == "secret"
    assert driver.checkbox_checked is True
    assert runner.context["auto_login_attempted"] is True


def test_auto_login_requires_env_credentials(monkeypatch, tmp_path):
    driver = FakeLoginDriver(state="login_required")
    runner = make_runner(monkeypatch, tmp_path, driver, account="", password="")

    with pytest.raises(AssertionError, match="缺少 AURO_LOGIN_ACCOUNT/AURO_LOGIN_PASSWORD"):
        runner.action_tap_my_account({
            "action": "tap_my_account",
            "attempts": 1,
            "attempt_wait": 0,
            "wait_after": 0,
        })


def test_tap_my_account_does_not_login_when_already_on_account_page(monkeypatch, tmp_path):
    driver = FakeLoginDriver(state="account_page")
    runner = make_runner(monkeypatch, tmp_path, driver)
    calls = []
    runner.perform_login = lambda reason="": calls.append(reason)

    runner.action_tap_my_account({
        "action": "tap_my_account",
        "attempts": 1,
        "attempt_wait": 0,
        "wait_after": 0,
    })

    assert calls == []
    assert driver.state == "account_page"


def test_auto_login_failure_does_not_loop_forever(monkeypatch, tmp_path):
    driver = FakeLoginDriver(state="login_required", login_success=False)
    runner = make_runner(monkeypatch, tmp_path, driver)

    with pytest.raises(AssertionError, match="未检测到登录成功状态"):
        runner.action_tap_my_account({
            "action": "tap_my_account",
            "attempts": 2,
            "attempt_wait": 0,
            "wait_after": 0,
        })

    assert runner.context["auto_login_attempted"] is True
