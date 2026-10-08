"""回归测试：夜跑前的音频前置检查。

背景（2026-09-30 起的夜跑回归）：
夜跑由 Windows 任务计划程序用 `-WakeToRun` 在睡眠唤醒后无人值守执行，
此时没有可用的交互式桌面会话，模拟器 DirectSound 后端初始化失败
（stderr: "dsound: Could not set cooperative level for window 0000000000000000"），
宿主音频回环（Windows 播放设备 -> CABLE Input -> 模拟器 host mic）送不出声音，
App 收不到语音就停在「未连接」，5 条 P0 翻译用例必挂：

    [P0][翻译]单向模式_中文转英文 / 日文转英文 / 英文转中文
    [P0][翻译]手机模式_点击录音_中文转英文 / 英文转中文

⚠️ 2026-10-08 完整实跑修正了上面的认识（见下），现在检查分三层：
  1. emulator_audio_health   —— 本次启动的 stderr 有没有 DirectSound 初始化失败
  2. emulator_host_mic_reachable —— hostmicoff/hostmicon 控制台命令是否可用
  3. emulator_loopback_level —— **实测**宿主播放能否被模拟器 MIC 收到（rms 判据）

第 3 层是关键：10/07 的失败 run 里 App 状态其实是「已连接」、音频也播了，
但页面文本为空 —— 即"回环开着却送的是静音"，前两层都看不见这种失败。
另：所有走音频注入的用例（含 PASS 的 `[音频]` 类）其实都没被 App 听到，
只是 `[音频]` 类从不断言转录内容，把静音掩盖了；`[翻译]` 类要求真实译文才暴露。

这些测试锁定夜跑必须**提前大声报出**这类环境问题，而不是烧掉约 50 分钟
再把环境噪声混进质量结论。
"""
import shutil
import struct

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
    """写 stderr 并把模块常量指向它；返回本次写入的起始偏移。"""
    monkeypatch.setattr(nr, "REPORT_ROOT", temp_root)
    path = temp_root / "emulator_stderr.log"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(nr, "EMULATOR_STDERR_PATH", path)
    return len(text.encode("utf-8"))


def test_audio_health_flags_dsound_failure_when_no_interactive_session(temp_root, monkeypatch):
    """0 号窗口句柄 = 没有交互式桌面会话，必须判定为音频不可用。"""
    _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE)
    ok, detail = nr.emulator_audio_health(HOST_CONFIG, stderr_offset=0)
    assert ok is False
    assert "DirectSound" in detail
    assert "交互式桌面会话" in detail


def test_audio_health_passes_on_clean_emulator_stderr(temp_root, monkeypatch):
    _write_stderr(temp_root, monkeypatch, "pc_memory_init: above 4g size: 40000000\n")
    ok, detail = nr.emulator_audio_health(HOST_CONFIG, stderr_offset=0)
    assert ok is True


def test_audio_health_skips_when_not_host_mode(temp_root, monkeypatch):
    """非 host 注入模式不依赖主机声卡，不应误报。"""
    _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE)
    ok, _ = nr.emulator_audio_health(
        {**HOST_CONFIG, "audio_injection_mode": "docker_pulse"}, stderr_offset=0
    )
    assert ok is True


def test_audio_health_skips_other_audio_backend(temp_root, monkeypatch):
    _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE)
    ok, _ = nr.emulator_audio_health(
        {**HOST_CONFIG, "emulator_audio_backend": "none"}, stderr_offset=0
    )
    assert ok is True


def test_audio_health_tolerates_missing_log(temp_root, monkeypatch):
    """还没写过 stderr 日志时不应因缺文件而炸掉夜跑。"""
    monkeypatch.setattr(nr, "REPORT_ROOT", temp_root)
    monkeypatch.setattr(nr, "EMULATOR_STDERR_PATH", temp_root / "emulator_stderr.log")
    ok, detail = nr.emulator_audio_health(HOST_CONFIG, stderr_offset=0)
    assert ok is True
    assert "无法判断" in detail


def test_stale_failure_from_previous_run_is_ignored(temp_root, monkeypatch):
    """跨夜追加的文件里，**上次**的失败不能算本次失败（本次修复的假阳性）。

    2026-10-08 实跑暴露：emulator_stderr.log 是追加写的且只有几 KB，
    按"最后 20000 字符"读取会把昨晚的 dsound 失败也算进来 → 42/42 全绿
    却误报音频不可用。正确做法是按启动前记录的偏移只读本次新增内容。
    """
    size = _write_stderr(temp_root, monkeypatch, DSPAWN_FAILURE)
    ok, detail = nr.emulator_audio_health(HOST_CONFIG, stderr_offset=size)
    assert ok is True, "上一次运行的失败不应算作本次失败"


def test_fresh_failure_after_baseline_is_still_caught(temp_root, monkeypatch):
    """本次启动新写入的失败必须仍能被抓到（别因为修假阳性而漏报）。"""
    size = _write_stderr(temp_root, monkeypatch, "pc_memory_init: ok\n")
    path = temp_root / "emulator_stderr.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(DSPAWN_FAILURE)
    ok, detail = nr.emulator_audio_health(HOST_CONFIG, stderr_offset=size)
    assert ok is False
    assert "DirectSound" in detail


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
    assert nr.check_audio_preconditions(HOST_CONFIG, stderr_offset=0) is False
    assert called == [], "音频已不可用时不应继续探测 host mic"


def test_check_preconditions_passes_when_both_ok(temp_root, monkeypatch):
    _write_stderr(temp_root, monkeypatch, "pc_memory_init: above 4g size: 40000000\n")

    class Result:
        returncode = 0
        stdout = "OK"
        stderr = ""

    monkeypatch.setattr(nr, "adb_target", lambda config, args, timeout=10: Result())
    # 回环电平探测要真跑 adb，这里单独打桩，保持前置检查测试无副作用。
    monkeypatch.setattr(nr, "emulator_loopback_level", lambda config, seconds=3.0: (True, "回环正常：stub"))
    assert nr.check_audio_preconditions(HOST_CONFIG, stderr_offset=0) is True


def test_check_preconditions_fails_when_loopback_is_silent(temp_root, monkeypatch):
    """回环"开着但送静音"必须判定失败 —— 这正是 10/07 那 5 条挂掉的真因。"""
    _write_stderr(temp_root, monkeypatch, "pc_memory_init: above 4g size: 40000000\n")

    class Result:
        returncode = 0
        stdout = "OK"
        stderr = ""

    monkeypatch.setattr(nr, "adb_target", lambda config, args, timeout=10: Result())
    monkeypatch.setattr(
        nr,
        "emulator_loopback_level",
        lambda config, seconds=3.0: (False, "回环电平异常：播放时 rms=2.0，静音基线 rms=3.0"),
    )
    assert nr.check_audio_preconditions(HOST_CONFIG, stderr_offset=0) is False


def _wav_with_samples(samples):
    """构造 tinycap 风格的 16-bit PCM WAV。"""
    body = struct.pack(f"<{len(samples)}h", *samples)
    header = bytearray(44)
    header[0:4] = b"RIFF"
    header[4:8] = (36 + len(body)).to_bytes(4, "little")
    header[8:12] = b"WAVE"
    header[36:40] = b"data"
    header[40:44] = len(body).to_bytes(4, "little")
    return bytes(header) + body


def test_loopback_rms_none_when_header_only(temp_root, monkeypatch):
    """44 字节空 WAV（tinycap 没起来）必须返回 None，不能当成"静音"。"""
    monkeypatch.setattr(nr, "REPORT_ROOT", temp_root)
    monkeypatch.setattr(nr, "adb_target", lambda config, args, timeout=10: _Ok())
    (temp_root / "_nightly_loopback_quiet.wav").write_bytes(_wav_with_samples([]))
    assert nr.loopback_rms(HOST_CONFIG, "/sdcard/x.wav", "quiet") is None


class _Ok:
    returncode = 0


def test_loopback_rms_measures_real_signal(temp_root, monkeypatch):
    """有声音的 WAV 应算出明显非零 rms。"""
    monkeypatch.setattr(nr, "REPORT_ROOT", temp_root)
    monkeypatch.setattr(nr, "adb_target", lambda config, args, timeout=10: _Ok())
    (temp_root / "_nightly_loopback_playing.wav").write_bytes(
        _wav_with_samples([1000, -1000] * 500)
    )
    rms = nr.loopback_rms(HOST_CONFIG, "/sdcard/x.wav", "playing")
    assert rms is not None and rms > 500


def test_loopback_level_skips_when_not_emulator(monkeypatch):
    ok, detail = nr.emulator_loopback_level({**HOST_CONFIG, "android_adb_serial": "10.0.0.1:5555"})
    assert ok is True
    assert "跳过" in detail


def test_loopback_level_skips_when_not_host_mode(monkeypatch):
    ok, detail = nr.emulator_loopback_level({**HOST_CONFIG, "audio_injection_mode": "docker_pulse"})
    assert ok is True
    assert "跳过" in detail


def test_loopback_level_tolerates_no_root(temp_root, monkeypatch):
    """拿不到 root 时应跳过而不是失败（不能因为探测手段不可用就拦夜跑）。"""
    class Denied:
        returncode = 1
        stdout = ""
        stderr = "adbd cannot run as root in production builds"

    monkeypatch.setattr(nr, "adb_target", lambda config, args, timeout=10: Denied())
    ok, detail = nr.emulator_loopback_level(HOST_CONFIG)
    assert ok is True
    assert "root" in detail


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
    monkeypatch.setattr(nr, "check_audio_preconditions", lambda config, stderr_offset=None: calls.append("precheck") or False)
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