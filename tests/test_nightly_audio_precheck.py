"""回归测试：夜跑前的音频前置检查。

背景（2026-09-30 起的夜跑回归）：
夜跑由 Windows 任务计划程序用 `-WakeToRun` 在睡眠唤醒后无人值守执行，
此时没有可用的交互式桌面会话，模拟器 DirectSound 后端初始化失败
（stderr: "dsound: Could not set cooperative level for window 0000000000000000"），
宿主音频回环（Windows 播放设备 -> CABLE Input -> 模拟器 host mic）送不出声音，
App 收不到语音就停在「未连接」，5 条 P0 翻译用例必挂：

    [P0][翻译]单向模式_中文转英文 / 日文转英文 / 英文转中文
    [P0][翻译]手机模式_点击录音_中文转英文 / 英文转中文

报错固定为 `等待翻译结果超时 ... visible_texts=['翻译','未连接',...]`，
runner 重试（resetting emulator host microphone bridge）对已损坏的设备无效。

这些测试锁定夜跑必须**提前大声报出**这类环境问题，而不是烧掉约 50 分钟
再把环境噪声混进质量结论。
"""
import shutil

import pytest

from core.config import PROJECT_ROOT
from scripts import nightly_run as nr


HOST_CONFIG = {
    "audio_injection_mode": "host",
    "emulator_audio_backend": "dsound",
    "android_adb_serial": "emulator-5554",
}


@pytest.fixture
def temp_root():
    """Workspace-local temp dir (pytest's tmp_path is blocked by the sandbox)."""
    root = PROJECT_ROOT / ".tmp" / "test_audio_precheck"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)

DSPAWN_FAILURE = (
    "dsound: Could not set cooperative level for window 0000000000000000\n"
    "dsound: Reason: An invalid parameter was passed to the returning function\n"
)


def _write_stderr(temp_root, monkeypatch, text):
    monkeypatch.setattr(nr, "REPORT_ROOT", temp_root)
    (temp_root / "emulator_stderr.log").write_text(text, encoding="utf-8")


def test_audio_health_flags_dsound_failure_when_no_interactive_session(temp_root, monkeypatch):
    """0 号窗口句柄 = 没有交互式桌面会话，必须判定为音频不可用。"""
    _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE)
    ok, detail = nr.emulator_audio_health(HOST_CONFIG)
    assert ok is False
    assert "DirectSound" in detail
    assert "交互式桌面会话" in detail


def test_audio_health_passes_on_clean_emulator_stderr(temp_root, monkeypatch):
    _write_stderr(temp_root, monkeypatch, "pc_memory_init: above 4g size: 40000000\n")
    ok, detail = nr.emulator_audio_health(HOST_CONFIG)
    assert ok is True


def test_audio_health_skips_when_not_host_mode(temp_root, monkeypatch):
    """非 host 注入模式不依赖主机声卡，不应误报。"""
    _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE)
    ok, _ = nr.emulator_audio_health({**HOST_CONFIG, "audio_injection_mode": "docker_pulse"})
    assert ok is True


def test_audio_health_skips_other_audio_backend(temp_root, monkeypatch):
    _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE)
    ok, _ = nr.emulator_audio_health({**HOST_CONFIG, "emulator_audio_backend": "none"})
    assert ok is True


def test_audio_health_tolerates_missing_log(temp_root, monkeypatch):
    """还没写过 stderr 日志时不应因缺文件而炸掉夜跑。"""
    monkeypatch.setattr(nr, "REPORT_ROOT", temp_root)
    ok, detail = nr.emulator_audio_health(HOST_CONFIG)
    assert ok is True
    assert "无法判断" in detail


def test_audio_health_only_reads_tail_of_log(temp_root, monkeypatch):
    """很老的失败不应永久污染判断：只看最近的日志窗口。"""
    _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE + ("x" * 40000))
    ok, _ = nr.emulator_audio_health(HOST_CONFIG)
    assert ok is True


def test_host_mic_probe_skips_for_real_device():
    """非模拟器目标不做 host mic 探测（AGENTS.md 禁止越界操作设备）。"""
    ok, detail = nr.emulator_host_mic_reachable(
        {"android_adb_serial": "R58M12345", "adb_exe": "adb"}
    )
    assert ok is True
    assert "跳过" in detail


def test_host_mic_probe_reports_console_rejection(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "error: no emulator detected"

    monkeypatch.setattr(nr, "adb_target", lambda config, args, timeout=10: Result())
    ok, detail = nr.emulator_host_mic_reachable(HOST_CONFIG)
    assert ok is False
    assert "hostmicoff" in detail


def test_host_mic_probe_passes_when_console_accepts(monkeypatch):
    class Result:
        returncode = 0
        stdout = "OK"
        stderr = ""

    monkeypatch.setattr(nr, "adb_target", lambda config, args, timeout=10: Result())
    ok, detail = nr.emulator_host_mic_reachable(HOST_CONFIG)
    assert ok is True


def test_check_preconditions_fails_fast_on_audio_failure(temp_root, monkeypatch):
    """音频不可用时整体检查必须返回 False，且不再去探测 mic。"""
    _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE)
    called = []
    monkeypatch.setattr(
        nr, "emulator_host_mic_reachable", lambda config: (called.append(1), (True, ""))[1]
    )
    assert nr.check_audio_preconditions(HOST_CONFIG) is False
    assert called == [], "音频已不可用时不应继续探测 host mic"


def test_check_preconditions_passes_when_both_ok(temp_root, monkeypatch):
    _write_stderr(temp_root, monkeypatch, "pc_memory_init: above 4g size: 40000000\n")

    class Result:
        returncode = 0
        stdout = "OK"
        stderr = ""

    monkeypatch.setattr(nr, "adb_target", lambda config, args, timeout=10: Result())
    assert nr.check_audio_preconditions(HOST_CONFIG) is True


def test_run_nightly_marks_audio_failure_as_environment_noise(monkeypatch):
    """夜跑必须把音频前置失败标注为环境噪声，避免污染质量结论。"""
    calls = []

    class Result:
        returncode = 1
        stdout = "FAILED tests/test_run_recordings.py::test_run_recording[P0-单向模式_中文转英文]"
        stderr = ""

    monkeypatch.setattr(nr, "load_config", lambda: dict(HOST_CONFIG))
    monkeypatch.setattr(nr, "qiniu_requested", lambda config: True)
    monkeypatch.setattr(nr, "clean_runtime_before_run", lambda config: None)
    monkeypatch.setattr(nr, "ensure_emulator", lambda config: None)
    monkeypatch.setattr(nr, "register_runtime_cleanup", lambda config: None)
    monkeypatch.setattr(nr, "ensure_target_app", lambda config: None)
    monkeypatch.setattr(nr, "ensure_appium", lambda config: None)
    monkeypatch.setattr(nr, "check_audio_preconditions", lambda config: calls.append("precheck") or False)
    monkeypatch.setattr(nr, "run_pytest", lambda config: Result())
    monkeypatch.setattr(nr, "write_run_log", lambda result: None)

    class Bundle:
        def glob(self, *a, **k):
            return []

    monkeypatch.setattr(nr, "latest_report_bundle", lambda: None)
    args = type("Args", (), {"notify": "never"})()
    with pytest.raises(RuntimeError):
        nr.run_nightly(args)
    assert calls == ["precheck"], "夜跑必须先做音频前置检查"