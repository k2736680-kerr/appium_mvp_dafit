"""回归测试：分享面板残留导致后续用例整段连带失败。

背景（2026-09-25 起的夜跑回归）：
`[P1]视频通话_中文转英文` 等录制最后一步点「分享邀请链接」，会唤起系统分享面板
（com.android.intentresolver）。该用例自身 PASSED，但面板不关闭且跨用例残留，
盖住 com.moyoung.auro.ai 的启动，使后续用例在 create_driver 阶段直接报
"Cannot start the 'com.moyoung.auro.ai' application / ... never started"。
09-25 起每晚固定 13 条失败（此前为 0），并因被误判为瞬态而反复重启 Appium。

这些测试锁定三处修复，防止再次回归。
"""
import ast
import sys
from pathlib import Path

from core import driver_factory
from core.config import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

import tests.test_run_recordings as T  # noqa: E402

APP = T.APP_PACKAGE
SHARE = "com.android.intentresolver"
RECORDINGS = PROJECT_ROOT / "recordings"


class FakeDevice:
    """模拟分享面板残留的设备状态。"""

    def __init__(self, fg=APP, terminate_works=True, back_works=True):
        self.fg = fg
        self.terminate_works = terminate_works
        self.back_works = back_works
        self.calls = []

    def current_package(self):
        return self.fg

    def terminate_app(self, pkg):
        self.calls.append(("terminate", pkg))
        if pkg == SHARE and self.terminate_works:
            self.fg = "com.android.launcher3"
        if pkg == APP:
            self.fg = "com.android.launcher3"

    def activate_app(self, pkg):
        self.calls.append(("activate", pkg))
        if pkg == APP and self.fg != SHARE:
            self.fg = APP

    def press_keycode(self, code):
        self.calls.append(("key", code))
        if code == 4 and self.fg == SHARE and self.back_works:
            self.fg = "com.android.launcher3"
        if code == 3:
            self.fg = "com.android.launcher3"


def _run_prepare(monkeypatch, device):
    monkeypatch.setattr(T, "driver_current_package", lambda driver: driver.current_package())
    monkeypatch.setattr(T, "ensure_app_home", lambda driver, *a, **k: None)
    T.prepare_case_start(device, start_mode="app")


def test_prepare_case_start_recovers_from_leftover_share_sheet(monkeypatch):
    """分享面板残留时，条目开头必须能把它清掉并把目标 App 拉回前台。"""
    device = FakeDevice(fg=SHARE, terminate_works=True)
    _run_prepare(monkeypatch, device)
    assert device.fg == APP


def test_prepare_case_start_falls_back_to_back_when_terminate_blocked(monkeypatch):
    """terminate 关不掉分享面板时必须退回 BACK，而不是直接判失败。"""
    device = FakeDevice(fg=SHARE, terminate_works=False, back_works=True)
    _run_prepare(monkeypatch, device)
    assert ("key", 4) in device.calls
    assert device.fg == APP


def test_prepare_case_start_is_cheap_on_clean_foreground(monkeypatch):
    """前台干净时不应产生任何多余的 terminate/BACK（避免拖慢夜跑）。"""
    device = FakeDevice(fg=APP)
    _run_prepare(monkeypatch, device)
    assert ("key", 4) not in device.calls
    assert ("key", 3) not in device.calls
    assert device.fg == APP


def test_cannot_start_is_not_treated_as_transient():
    """"Cannot start the ... application" 是脏前台，不是瞬态，不该重启 Appium。"""
    from selenium.common.exceptions import WebDriverException

    exc = WebDriverException(
        "Message: Cannot start the 'com.moyoung.auro.ai' application. "
        "Original error: '*' or 'com.moyoung.auro.ai.*' never started."
    )
    assert driver_factory.is_transient_session_error(exc) is False


def test_share_recordings_get_cleanup_step_appended():
    """点过「分享邀请链接」的录制，用例末尾必须自动补收尾清理步骤。"""
    targets = [
        RECORDINGS / "[P1]视频通话_中文转英文.py",
        RECORDINGS / "[P1]语音通话_中文转英文.py",
    ]
    for path in targets:
        assert path.exists(), f"缺少录制文件: {path}"
        case = T.recording_to_case(path)
        actions = [step.get("action") for step in case["steps"]]
        assert "dismiss_system_surfaces" in actions, f"{path.name} 缺少收尾清理步骤"


def test_non_share_recordings_are_not_polluted():
    """没有分享行为的录制不应被插入清理步骤。"""
    case = T.recording_to_case(RECORDINGS / "[P2]操作指引.py")
    actions = [step.get("action") for step in case["steps"]]
    assert "dismiss_system_surfaces" not in actions


def test_runner_supports_dismiss_system_surfaces_action():
    """runner 必须实现该 action，否则录制里补的步骤会在执行时报错。"""
    source = (PROJECT_ROOT / "core" / "runner.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert '"dismiss_system_surfaces"' in source
    assert "def action_dismiss_system_surfaces" in source


class _SplashThenHomeDevice:
    """App 在前台但仍停在 Splash Screen：mCurrentFocus=null，BACK 被拒绝。"""

    def __init__(self, splash_reads=2):
        self.splash_reads = splash_reads
        self.reads = 0
        self.back_calls = 0

    def current_package(self):
        return APP

    @property
    def page_source(self):
        self.reads += 1
        return "<hierarchy>splash</hierarchy>" if self.reads <= self.splash_reads \
            else "<hierarchy>翻译中心</hierarchy>"

    def press_keycode(self, code):
        self.back_calls += 1
        if self.reads <= self.splash_reads:
            from selenium.common.exceptions import InvalidElementStateException
            raise InvalidElementStateException(
                "Cannot generate key press event for key code 4"
            )

    def activate_app(self, pkg):
        pass

    def terminate_app(self, pkg):
        pass

    def save_screenshot(self, path):
        pass


def test_ensure_app_home_tolerates_back_rejection_during_splash(monkeypatch):
    """Splash 期间 BACK 抛 InvalidElementStateException 不应让整条用例失败。

    真机复现：App 已在前台但 Splash Screen 未消失（mCurrentFocus=null），
    uiautomator2 拒绝注入按键码 4，异常从 ensure_app_home 逃逸，导致
    prepare_case_start 整个失败。
    """
    from selenium.common.exceptions import InvalidElementStateException

    monkeypatch.setattr(T, "driver_current_package", lambda d: d.current_package())
    monkeypatch.setattr(T, "save_prelude_debug", lambda d, name: None)
    monkeypatch.setattr(T, "save_failure_debug", lambda *a, **k: None)

    device = _SplashThenHomeDevice(splash_reads=2)
    # 不应抛 InvalidElementStateException
    T.ensure_app_home(device, timeout=2, max_back=6)
    assert device.back_calls >= 1


class _SlowColdBootDevice:
    """冷启动：Splash 持续 splash_seconds，期间 mCurrentFocus=null、BACK 被拒。"""

    def __init__(self, splash_seconds):
        self.splash_seconds = splash_seconds
        self.t0 = None
        self.back_calls = 0

    def _elapsed(self):
        import time
        if self.t0 is None:
            self.t0 = time.time()
        return time.time() - self.t0

    def current_package(self):
        return APP

    @property
    def page_source(self):
        return ("<hierarchy>splash</hierarchy>" if self._elapsed() < self.splash_seconds
                else "<hierarchy>翻译中心</hierarchy>")

    def press_keycode(self, code):
        self.back_calls += 1
        if self._elapsed() < self.splash_seconds:
            from selenium.common.exceptions import InvalidElementStateException
            raise InvalidElementStateException(
                "Cannot generate key press event for key code 4"
            )

    def activate_app(self, pkg):
        pass

    def terminate_app(self, pkg):
        pass

    def save_screenshot(self, path):
        pass


def test_ensure_app_home_waits_out_slow_cold_boot(monkeypatch):
    """冷启动比单次 1s 等待更久时，必须等 App 起来，而不是耗完 BACK 次数后带病执行。

    真机复现（2026-09-29 13:33 夜跑）：首条用例在 Splash 未消失时就往下点，
    报 `找不到或无法点击元素: descriptionContains("翻译中心")`。
    """
    monkeypatch.setattr(T, "driver_current_package", lambda d: d.current_package())
    monkeypatch.setattr(T, "save_prelude_debug", lambda d, name: None)
    monkeypatch.setattr(T, "save_failure_debug", lambda *a, **k: None)

    device = _SlowColdBootDevice(splash_seconds=5)
    T.ensure_app_home(device, timeout=20, max_back=6)  # 不应抛异常
    assert device.back_calls == 0, "Splash 期间不该反复按 BACK"


def test_nightly_flags_dirty_foreground_cascade():
    """夜跑必须自己报警脏前台连带失败，避免这类回归静默混进夜跑结论。"""
    from types import SimpleNamespace

    from scripts import nightly_run as N

    dirty = SimpleNamespace(
        returncode=1,
        stdout=(
            "E  WebDriverException: Message: Cannot start the 'com.moyoung.auro.ai' "
            "application. Original error: '*' or 'com.moyoung.auro.ai.*' never started."
        ),
        stderr="",
    )
    clean = SimpleNamespace(
        returncode=0,
        stdout="test_run_recording[P2-操作指引] PASSED [100%]",
        stderr="",
    )

    hits, sample = N.summarize_cascade_risk(dirty)
    assert hits > 0 and sample
    assert N.summarize_cascade_risk(clean)[0] == 0


