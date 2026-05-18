import json
from tempfile import TemporaryDirectory

from runner.locator import rank_locator, warn_unstable_locator
from runner.report import StepReportManager
from runner.state import PageState
from runner.verifier import StepVerifier


def make_state(source: str) -> PageState:
    return PageState(
        page_source=source,
        screenshot_png=b"png-bytes",
        visible_texts=[],
        source_hash=str(hash(source)),
    )


def test_page_signature_uses_standard_metadata_without_page_patch():
    case_data = {
        "pages": {
            "steps_detail": {
                "page_name": "步数详情页",
                "signature": {
                    "must_have_texts": ["步数"],
                    "any_have_texts": ["日", "周", "月"],
                    "must_have_elements": ["返回按钮"],
                    "min_match": 2,
                },
            }
        },
        "elements": {
            "steps_detail.back": {
                "name": "返回按钮",
                "locator": {"by": "accessibility_id", "value": "返回按钮"},
            }
        },
    }
    verifier = StepVerifier(case_data=case_data)
    state = make_state('<node text="步数"/><node text="周"/><node content-desc="返回按钮"/>')

    result = verifier.assert_page_signature("steps_detail", state)

    assert result["passed"] is True
    assert result["strategy"] == "page_signature"


def test_tab_default_strategy_is_content_changed_plus_no_crash():
    case_data = {
        "elements": {
            "steps_detail.week_tab": {
                "name": "周",
                "role": "tab",
            }
        }
    }
    verifier = StepVerifier(case_data=case_data)

    result = verifier.verify_step(
        {"action": "tap", "target": "steps_detail.week_tab"},
        make_state("<node text='日'/>"),
        make_state("<node text='周'/>"),
    )

    assert result["passed"] is True
    assert result["strategy"] == "content_changed+no_crash"


def test_instance_only_locator_is_ranked_last_and_warned():
    stable = {"by": "android_uiautomator", "value": 'new UiSelector().text("周")'}
    unstable = {
        "by": "android_uiautomator",
        "value": 'new UiSelector().className("android.widget.LinearLayout").instance(5)',
    }

    assert rank_locator(stable) < rank_locator(unstable)
    assert warn_unstable_locator(unstable) == [
        "unstable_locator: android_uiautomator instance-only selector"
    ]


def test_report_saves_failure_evidence_and_json_summary():
    before = make_state("<node text='before'/>")
    after = make_state("<node text='after'/>")

    with TemporaryDirectory() as tmpdir:
        report = StepReportManager("case_001", root=tmpdir)
        record = report.save_step_result(
            index=1,
            step={
                "action": "swipe",
                "direction": "up",
                "verify": {"strategy": "scroll_reveal", "expected_texts": ["目标"]},
            },
            before_state=before,
            after_state=after,
            verify_result={
                "passed": False,
                "strategy": "scroll_reveal",
                "checks": [],
                "failure_reason": "scroll target texts not revealed: ['目标']",
            },
        )

        report_json = json.loads((report.report_dir / "report.json").read_text(encoding="utf-8"))

    assert record["passed"] is False
    assert record["before_screenshot"] == "screenshots/step_001_before.png"
    assert record["after_page_source"] == "page_sources/step_001_after.xml"
    assert report_json["failed_steps"] == 1
    assert report_json["steps"][0]["failure_reason"] == "scroll target texts not revealed: ['目标']"
