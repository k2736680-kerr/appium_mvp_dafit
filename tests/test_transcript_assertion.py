"""回归测试：`[音频]` 类用例必须有"真的听到并转写"的断言。

背景（2026-10-08 审计）：
`[音频]` 类用例（会议记录/语音记录/会议记录_暂停标记）原本是纯点击序列，
**从不校验转录结果**。于是：
- 9/29 ~ 10/07 连续 8 个夜跑里，转录文本完全为空（音频回环送静音），
  但 `会议记录`/`语音记录` 每晚都报 PASSED —— 假绿，掩盖了真实故障。
- 整个报告期共 11 次「转录为空却 PASS」。

这里锁定：这类用例必须生成 assert_transcript 步骤，且断言要能真的判定失败。
"""
import shutil

import pytest

from core.config import PROJECT_ROOT
from core.recording_rules import (
    TRANSCRIPT_EXPECTED_MARKERS,
    augment_recording_steps,
    build_transcript_assert_step,
    parse_case_labels,
)
from core import runner as runner_mod


@pytest.fixture
def temp_root():
    root = PROJECT_ROOT / ".tmp" / "test_transcript_assert"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- 步骤生成 ---------------------------------------------------------------

def _audio_case_steps():
    """会议记录：录制里是「点录制 -> 点结束」，中间不插音频。"""
    return [
        {"action": "click", "name": "点击会议记录", "locator": {"by": "x", "value": "y"}},
        {"action": "start_meeting_recording", "name": "点击会议记录-底部录制按钮"},
        {"action": "click", "name": "点击结束录制", "locator": {"by": "x", "value": "y"}},
    ]


def test_audio_case_gets_transcript_assertion():
    """`[音频]` 用例必须补齐转写断言，否则静音也会 PASS。"""
    steps, meta = augment_recording_steps(
        "[P0][音频]会议记录", _audio_case_steps(), enable_audio=True, enable_translation=False
    )
    actions = [s["action"] for s in steps]
    assert "assert_transcript" in actions, "音频用例缺少转写断言（假绿风险）"
    assert "assert_translation" not in actions, "无翻译标签的用例不应生成翻译断言"


def test_translation_case_still_gets_translation_assertion():
    """带 [翻译] 的用例保持原行为，不要被新分支顶掉。"""
    steps, meta = augment_recording_steps(
        "[P0][翻译]单向模式_中文转英文",
        [
            {"action": "click", "name": "点击底部麦克风按钮", "locator": {"by": "x", "value": "y"}},
            {"action": "click", "name": "再次点击底部麦克风按钮", "locator": {"by": "x", "value": "y"}},
        ],
        enable_audio=True,
        enable_translation=True,
    )
    actions = [s["action"] for s in steps]
    assert "assert_translation" in actions
    assert "assert_transcript" not in actions or actions.index("assert_translation") < actions.index(
        "assert_transcript"
    ), "翻译用例应以翻译断言为准"


def test_case_without_audio_gets_no_transcript_assertion():
    """没启音频的用例不该凭空多出转写断言。"""
    steps, _ = augment_recording_steps(
        "[P1]修改用户名",
        [{"action": "click", "name": "点击头像", "locator": {"by": "x", "value": "y"}}],
        enable_audio=False,
        enable_translation=False,
    )
    assert "assert_transcript" not in [s["action"] for s in steps]


def test_transcript_markers_use_stable_substring():
    """判据必须是稳定子串：语音识别对句首有差异，整句写死会误报。"""
    markers = [m.lower() for m in TRANSCRIPT_EXPECTED_MARKERS]
    assert any("translation test" in m for m in markers)
    step = build_transcript_assert_step()
    assert step["expected_markers"], "断言步骤必须携带 expected_markers"
    assert step["action"] == "assert_transcript"


# --- 断言行为 ---------------------------------------------------------------

class _FakeDriver:
    def __init__(self, sources):
        self._sources = list(sources)
        self._last = self._sources[-1] if self._sources else ""

    @property
    def page_source(self):
        if self._sources:
            self._last = self._sources.pop(0)
        return self._last


class _FakeArtifacts:
    def __init__(self):
        self.saved = []

    def save(self, driver, index, name):
        self.saved.append(name)


def _make_runner(sources):
    runner = runner_mod.CaseRunner.__new__(runner_mod.CaseRunner)
    runner.driver = _FakeDriver(sources)
    runner.artifacts = _FakeArtifacts()
    runner.context = {}
    runner.find_blocker = lambda page_source: None
    return runner


TRANSCRIPT_XML = (
    '<hierarchy><android.view.View content-desc="Speaker&#10;00:00:09&#10;'
    'Hello, nice to meet you. This is a translation test."/></hierarchy>'
)
EMPTY_XML = (
    '<hierarchy><android.view.View content-desc="翻译"/>'
    '<android.view.View content-desc="未连接"/></hierarchy>'
)


def test_assert_transcript_passes_when_transcript_present():
    runner = _make_runner([TRANSCRIPT_XML])
    runner.action_assert_transcript({
        "expected_markers": list(TRANSCRIPT_EXPECTED_MARKERS),
        "timeout": 1,
        "poll_interval": 0.01,
    })


def test_assert_transcript_fails_when_silence():
    """这正是 9/29~10/07 那 8 个夜跑的情形：转录为空必须判失败，不能报绿。"""
    runner = _make_runner([EMPTY_XML])
    with pytest.raises(AssertionError) as exc:
        runner.action_assert_transcript({
            "expected_markers": list(TRANSCRIPT_EXPECTED_MARKERS),
            "timeout": 0.3,
            "poll_interval": 0.05,
        })
    assert "转写超时" in str(exc.value)
    assert "blocked_assert_transcript" in runner.artifacts.saved


def test_assert_transcript_tolerates_stt_variation():
    """语音识别的小差异（Hello. / Oh, / 省略 Hello）不应导致误报。"""
    variants = [
        'content-desc="Speaker&#10;00:00:09&#10;Hello. Nice to meet you. This is a translation test."',
        'content-desc="Oh, nice to meet you. This is a translation test."',
        'content-desc="Nice to meet you. This is a translation test."',
    ]
    for variant in variants:
        runner = _make_runner([f"<hierarchy><android.view.View {variant}/></hierarchy>"])
        runner.action_assert_transcript({
            "expected_markers": list(TRANSCRIPT_EXPECTED_MARKERS),
            "timeout": 1,
            "poll_interval": 0.01,
        })


def test_assert_transcript_requires_markers():
    runner = _make_runner([TRANSCRIPT_XML])
    with pytest.raises(ValueError):
        runner.action_assert_transcript({"timeout": 1})