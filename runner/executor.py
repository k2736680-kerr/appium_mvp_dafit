import time
from typing import Any

from runner.actions import ActionExecutor
from runner.report import StepReportManager
from runner.state import capture_state
from runner.verifier import StepVerifier


class UnifiedStepExecutor:
    def __init__(self, driver, case_data: dict[str, Any]):
        self.driver = driver
        self.case_data = case_data
        self.case_id = case_data.get("case_id", "unnamed_case")
        self.elements = self._collect_elements(case_data)
        self.actions = ActionExecutor(driver, self.elements)
        self.verifier = StepVerifier(driver, case_data)
        self.report = StepReportManager(self.case_id, element_registry=self.elements)

    def run(self):
        for index, step in enumerate(self.case_data.get("steps", []), start=1):
            self.execute_step(index, step)

    def execute_step(self, index: int, step: dict[str, Any]) -> dict[str, Any]:
        before_state = capture_state(self.driver)
        action_result = {}
        action_error = ""
        verify_result = None

        try:
            action_result = self.actions.execute(step)
            self.wait_after_action(step)
            after_state = capture_state(self.driver)
            verify_result = self.verifier.verify_step(step, before_state, after_state)
        except Exception as exc:
            action_error = str(exc)
            after_state = capture_state(self.driver)
            if not verify_result:
                verify_result = {
                    "passed": False,
                    "strategy": "action",
                    "checks": [],
                    "failure_reason": action_error,
                }

        record = self.report.save_step_result(
            index=index,
            step=step,
            before_state=before_state,
            after_state=after_state,
            verify_result=verify_result,
            action_result=action_result,
            action_error=action_error,
        )
        if not record["passed"]:
            raise AssertionError(
                f"step {index} failed: action={step.get('action')}, "
                f"target={step.get('target')}, verify={record.get('verify_strategy')}, "
                f"reason={record.get('failure_reason')}"
            )
        return record

    def wait_after_action(self, step: dict[str, Any]):
        if step.get("action") in {"wait", "sleep"}:
            return
        time.sleep(float(step.get("wait_after", step.get("wait_after_action", 1))))

    def _collect_elements(self, case_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        elements: dict[str, dict[str, Any]] = {}
        for container in (
            case_data.get("elements"),
            case_data.get("app_map", {}).get("elements") if isinstance(case_data.get("app_map"), dict) else None,
            case_data.get("component_tree", {}).get("elements") if isinstance(case_data.get("component_tree"), dict) else None,
        ):
            if isinstance(container, dict):
                for element_id, element in container.items():
                    if isinstance(element, dict):
                        data = dict(element)
                        data.setdefault("element_id", element_id)
                        elements[str(data["element_id"])] = data
            elif isinstance(container, list):
                for element in container:
                    if isinstance(element, dict) and (element.get("element_id") or element.get("id")):
                        elements[str(element.get("element_id") or element.get("id"))] = element
        return elements
