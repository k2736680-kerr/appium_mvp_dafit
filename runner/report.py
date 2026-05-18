import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT
from runner.locator import build_locator
from runner.state import PageState


def safe_name(value: str) -> str:
    keep = []
    for ch in str(value):
        if ch.isalnum() or ch in "._-":
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep).strip("_")[:120] or "item"


class StepReportManager:
    def __init__(self, case_id: str, root: str | Path | None = None, element_registry: Any = None):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(root or os.environ.get("APPIUM_STEP_REPORTS_ROOT") or PROJECT_ROOT / "data" / "reports")
        self.report_dir = base / f"{safe_name(case_id)}_{timestamp}"
        self.screenshots_dir = self.report_dir / "screenshots"
        self.sources_dir = self.report_dir / "page_sources"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.case_id = case_id
        self.element_registry = element_registry
        self.steps: list[dict[str, Any]] = []
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def save_step_result(
        self,
        index: int,
        step: dict[str, Any],
        before_state: PageState,
        after_state: PageState,
        verify_result: dict[str, Any] | None,
        action_result: dict[str, Any] | None = None,
        action_error: str = "",
    ) -> dict[str, Any]:
        prefix = f"step_{index:03d}"
        before_screenshot = self.save_screenshot(before_state, f"{prefix}_before.png")
        after_screenshot = self.save_screenshot(after_state, f"{prefix}_after.png")
        before_source = self.save_page_source(before_state, f"{prefix}_before.xml")
        after_source = self.save_page_source(after_state, f"{prefix}_after.xml")

        locator_info = build_locator(step.get("target"), self.element_registry, step)
        locator = locator_info.get("locator") or step.get("locator")
        strategy = ""
        failure_reason = ""
        passed = True
        if verify_result:
            strategy = verify_result.get("strategy", "")
            failure_reason = verify_result.get("failure_reason", "")
            passed = bool(verify_result.get("passed", False))
        if action_error:
            passed = False
            failure_reason = action_error

        record = {
            "step_index": index,
            "name": step.get("name") or step.get("action") or f"step_{index}",
            "action": step.get("action"),
            "target": step.get("target"),
            "locator": locator,
            "locator_rank": locator_info.get("rank"),
            "locator_warnings": locator_info.get("warnings", []),
            "unstable_locator": any("unstable_locator" in item for item in locator_info.get("warnings", [])),
            "verify_strategy": strategy,
            "passed": passed,
            "failure_reason": failure_reason,
            "verify_result": verify_result or {},
            "action_result": action_result or {},
            "before_screenshot": before_screenshot,
            "after_screenshot": after_screenshot,
            "before_page_source": before_source,
            "after_page_source": after_source,
            "before_state": before_state.to_report_dict(),
            "after_state": after_state.to_report_dict(),
        }
        self.steps.append(record)
        self.write_json()
        self.generate_markdown_report()
        return record

    def save_screenshot(self, state: PageState, filename: str) -> str:
        path = self.screenshots_dir / filename
        if state.screenshot_png:
            path.write_bytes(state.screenshot_png)
            return self._rel(path)
        return ""

    def save_page_source(self, state: PageState, filename: str) -> str:
        path = self.sources_dir / filename
        path.write_text(state.page_source or state.page_source_error or "", encoding="utf-8")
        return self._rel(path)

    def write_json(self):
        payload = {
            "case_id": self.case_id,
            "started_at": self.started_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "report_dir": str(self.report_dir),
            "total_steps": len(self.steps),
            "failed_steps": len([step for step in self.steps if not step.get("passed")]),
            "steps": self.steps,
        }
        (self.report_dir / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def generate_markdown_report(self):
        total = len(self.steps)
        failed = len([step for step in self.steps if not step.get("passed")])
        lines = [
            f"# Appium Step Report: {self.case_id}",
            "",
            f"- Started: {self.started_at}",
            f"- Updated: {datetime.now().isoformat(timespec='seconds')}",
            f"- Steps: {total}",
            f"- Failed: {failed}",
            "",
            "## Steps",
            "",
        ]
        for step in self.steps:
            status = "PASS" if step.get("passed") else "FAIL"
            lines.extend(
                [
                    f"### {step['step_index']:03d}. {step['name']} [{status}]",
                    "",
                    f"- Action: `{step.get('action')}`",
                    f"- Target: `{step.get('target')}`",
                    f"- Verify: `{step.get('verify_strategy')}`",
                    f"- Locator: `{json.dumps(step.get('locator'), ensure_ascii=False)}`",
                    f"- Unstable locator: `{step.get('unstable_locator')}`",
                ]
            )
            if step.get("failure_reason"):
                lines.append(f"- Failure: {step['failure_reason']}")
            if step.get("before_screenshot"):
                lines.append(f"- Before screenshot: [{step['before_screenshot']}]({step['before_screenshot']})")
            if step.get("after_screenshot"):
                lines.append(f"- After screenshot: [{step['after_screenshot']}]({step['after_screenshot']})")
            lines.extend(
                [
                    f"- Before source: [{step['before_page_source']}]({step['before_page_source']})",
                    f"- After source: [{step['after_page_source']}]({step['after_page_source']})",
                    "",
                ]
            )
        (self.report_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.report_dir).as_posix()
