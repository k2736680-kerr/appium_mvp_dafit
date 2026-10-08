"""回归测试：夜跑日志的中文编码。

背景：
本机 locale 是 cp936（`locale.getencoding()`），`scripts/nightly_run.py` 原本
对所有子进程都用 `SUBPROCESS_ENCODING = cp936` 解码。但 pytest 子进程输出的是
UTF-8，父进程按 GBK 解码后再以 UTF-8 写入日志，中文就变成：

    [STEP] 1: 鐐瑰嚮棣栭〉-缈昏瘧涓蹇 | action=click

原意应为 `[STEP] 1: 点击首页-翻译中心 | action=click`。且乱码里出现
U+FFFD 替换字符，说明**已经丢信息、无法事后反解**，只能在写日志前修好管道。

受影响的 `reports/nightly_logs/nightly_*.log` 让夜跑排查非常痛苦
（用例名、[STEP]、断言文案全乱）。这些测试锁定修复不再回归。
"""
import os
import sys

from scripts import nightly_run as nr


SAMPLE = "[STEP] 1: 点击首页-翻译中心 | action=click"


def _child_env():
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _command():
    return [sys.executable, "-c", f"print({SAMPLE!r})"]


def test_run_accepts_text_encoding_override():
    """run() 必须支持按调用方指定编码，否则没法只修 pytest 这一路。"""
    import inspect

    assert "text_encoding" in inspect.signature(nr.run).parameters


def test_utf8_override_decodes_chinese_correctly():
    """显式按 utf-8 解码时，中文必须原样还原。"""
    result = nr.run(_command(), env=_child_env(), text_encoding="utf-8")
    assert result.returncode == 0
    assert SAMPLE in result.stdout


def test_default_cp936_decoding_still_mojibakes_on_this_host():
    """反证：沿用默认 locale 编码仍会乱码 —— 说明这个修复是必要的。

    （若将来本机 locale 变成 UTF-8，这个断言会失败，那时可直接删掉本测试。）
    """
    if nr.SUBPROCESS_ENCODING.lower().replace("-", "") not in {"cp936", "gbk", "gb2312", "gb18030"}:
        import pytest

        pytest.skip("本机 locale 已是 UTF-8，不再有这个乱码问题")
    result = nr.run(_command(), env=_child_env())
    assert SAMPLE not in result.stdout


def test_run_pytest_forces_utf8_child_and_decode(monkeypatch):
    """run_pytest 必须同时设置子进程 UTF-8 输出 + 父进程 UTF-8 解码。"""
    captured = {}

    class Result:
        returncode = 0
        stdout = SAMPLE
        stderr = ""

    def fake_run(command, timeout=None, env=None, text_encoding=None):
        captured["command"] = command
        captured["env"] = env
        captured["text_encoding"] = text_encoding
        return Result()

    monkeypatch.setattr(nr, "run", fake_run)
    monkeypatch.setattr(nr, "PROJECT_ROOT", nr.PROJECT_ROOT)

    config = {
        "pytest_timeout_seconds": 10,
        "appium_args": [],
    }
    nr.run_pytest(config)

    assert captured["text_encoding"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"