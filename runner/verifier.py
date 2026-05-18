import time
from typing import Any

from selenium.common.exceptions import WebDriverException

from core.config import APP_PACKAGE
from runner.locator import build_locator, to_appium_locator
from runner.state import PageState, capture_state


CRASH_MARKERS = (
    "keeps stopping",
    "isn't responding",
    "has stopped",
    "Unfortunately",
    "应用无响应",
    "已停止运行",
)


class StepVerifier:
    def __init__(self, driver=None, case_data: dict[str, Any] | None = None):
        self.driver = driver
        self.case_data = case_data or {}
        self.pages = self._collect_pages(self.case_data)
        self.elements = self._collect_elements(self.case_data)

    def verify_step(
        self,
        step: dict[str, Any],
        before_state: PageState,
        after_state: PageState,
    ) -> dict[str, Any]:
        checks = self._resolve_checks(step)
        results = []
        for check in checks:
            results.append(self._run_check(check, step, before_state, after_state))

        passed = all(item["passed"] for item in results)
        return {
            "passed": passed,
            "strategy": "+".join(item["strategy"] for item in results),
            "checks": results,
            "failure_reason": "; ".join(item["reason"] for item in results if not item["passed"]),
        }

    def assert_page_signature(self, page_id: str, after_state: PageState | None = None) -> dict[str, Any]:
        state = after_state or capture_state(self.driver)
        signature = self._page_signature(page_id)
        if not signature:
            return self._result("page_signature", False, f"page signature not configured: {page_id}")

        source = state.page_source or ""
        matched = []
        failures = []

        for text in signature.get("must_have_texts", []):
            if self._contains(source, text):
                matched.append(f"text:{text}")
            else:
                failures.append(f"missing must_have_text: {text}")

        any_texts = signature.get("any_have_texts", [])
        if any_texts:
            any_hits = [text for text in any_texts if self._contains(source, text)]
            matched.extend(f"any_text:{text}" for text in any_hits)
            any_min = int(signature.get("any_min", signature.get("any_have_min", 1)))
            if len(any_hits) < any_min:
                failures.append(f"any_have_texts hit {len(any_hits)}/{any_min}: {any_texts}")

        for element_name in signature.get("must_have_elements", []):
            if self._element_name_present(element_name, source):
                matched.append(f"element:{element_name}")
            else:
                failures.append(f"missing must_have_element: {element_name}")

        min_match = signature.get("min_match")
        if min_match is not None and len(matched) < int(min_match):
            failures.append(f"signature matched {len(matched)}, min_match={min_match}")

        if state.page_source_error:
            failures.append(f"page source unavailable: {state.page_source_error}")

        return self._result(
            "page_signature",
            not failures,
            "; ".join(failures),
            {"page_id": page_id, "matched": matched, "signature": signature},
        )

    def assert_exists(self, expected: dict[str, Any], after_state: PageState) -> dict[str, Any]:
        texts = self._expected_texts(expected)
        source = after_state.page_source or ""
        missing = [text for text in texts if not self._contains(source, text)]
        if texts:
            return self._result("exists", not missing, f"missing texts: {missing}" if missing else "", {"texts": texts})

        locator = self._expected_locator(expected)
        if locator:
            found = self._driver_element_exists(locator)
            return self._result("exists", found, f"missing element: {locator}" if not found else "", {"locator": locator})

        return self._result("exists", False, "exists requires expected_texts, expected_text, or locator")

    def assert_not_exists(self, expected: dict[str, Any], after_state: PageState) -> dict[str, Any]:
        texts = self._expected_texts(expected)
        source = after_state.page_source or ""
        present = [text for text in texts if self._contains(source, text)]
        if texts:
            return self._result("not_exists", not present, f"unexpected texts: {present}" if present else "", {"texts": texts})

        locator = self._expected_locator(expected)
        if locator:
            found = self._driver_element_exists(locator)
            return self._result("not_exists", not found, f"unexpected element: {locator}" if found else "", {"locator": locator})

        return self._result("not_exists", False, "not_exists requires expected_texts, expected_text, or locator")

    def assert_text_contains(self, expected: dict[str, Any], after_state: PageState) -> dict[str, Any]:
        return self.assert_exists(expected, after_state) | {"strategy": "text_contains"}

    def assert_selected(self, step: dict[str, Any], after_state: PageState) -> dict[str, Any]:
        locator = self._expected_locator(step)
        if locator and self.driver:
            try:
                by, value = to_appium_locator(locator)
                element = self.driver.find_element(by, value)
                selected = str(element.get_attribute("selected")).lower() == "true"
                return self._result("selected", selected, f"element is not selected: {locator}" if not selected else "")
            except Exception as exc:
                return self._result("selected", False, f"selected check failed: {exc}")

        target = step.get("target") or step.get("expected_text") or step.get("text")
        source = after_state.page_source or ""
        selected = bool(target and target in source and 'selected="true"' in source)
        return self._result("selected", selected, f"selected state not found for target: {target}" if not selected else "")

    def assert_content_changed(self, before_state: PageState, after_state: PageState) -> dict[str, Any]:
        changed = before_state.source_hash != after_state.source_hash
        if not changed and before_state.visible_texts != after_state.visible_texts:
            changed = True
        return self._result(
            "content_changed",
            changed,
            "page source hash and visible texts did not change" if not changed else "",
            {"before_hash": before_state.source_hash, "after_hash": after_state.source_hash},
        )

    def assert_scroll_reveal(self, expected: dict[str, Any], before_state: PageState, after_state: PageState) -> dict[str, Any]:
        texts = self._expected_texts(expected)
        source = after_state.page_source or ""
        if texts:
            missing = [text for text in texts if not self._contains(source, text)]
            return self._result(
                "scroll_reveal",
                not missing,
                f"scroll target texts not revealed: {missing}" if missing else "",
                {"expected_texts": texts},
            )
        content_result = self.assert_content_changed(before_state, after_state)
        content_result["strategy"] = "scroll_reveal"
        return content_result

    def assert_dialog_opened(self, expected: dict[str, Any], before_state: PageState, after_state: PageState) -> dict[str, Any]:
        texts = self._expected_texts(expected)
        if texts:
            result = self.assert_exists({"expected_texts": texts}, after_state)
            result["strategy"] = "dialog_opened"
            return result
        content_result = self.assert_content_changed(before_state, after_state)
        content_result["strategy"] = "dialog_opened"
        return content_result

    def assert_dialog_closed(self, expected: dict[str, Any], before_state: PageState, after_state: PageState) -> dict[str, Any]:
        texts = self._expected_texts(expected)
        if texts:
            result = self.assert_not_exists({"expected_texts": texts}, after_state)
            result["strategy"] = "dialog_closed"
            return result
        content_result = self.assert_content_changed(before_state, after_state)
        content_result["strategy"] = "dialog_closed"
        return content_result

    def assert_toast(self, expected: dict[str, Any], after_state: PageState) -> dict[str, Any]:
        texts = self._expected_texts(expected)
        if not texts:
            return self.assert_no_crash(after_state) | {"strategy": "toast"}

        timeout = float(expected.get("timeout", 3))
        deadline = time.time() + timeout
        state = after_state
        while True:
            source = state.page_source or ""
            if any(self._contains(source, text) for text in texts):
                return self._result("toast", True, "", {"expected_texts": texts})
            if not self.driver or time.time() >= deadline:
                break
            time.sleep(0.2)
            state = capture_state(self.driver)
        return self._result("toast", False, f"toast text not found: {texts}", {"expected_texts": texts})

    def assert_input_value(self, step: dict[str, Any], after_state: PageState) -> dict[str, Any]:
        expected = step.get("expected_value", step.get("text", ""))
        if expected and self._contains(after_state.page_source or "", expected):
            return self._result("input_value", True, "", {"expected_value": expected})

        locator = self._expected_locator(step)
        if locator and self.driver:
            try:
                by, value = to_appium_locator(locator)
                element = self.driver.find_element(by, value)
                actual = element.get_attribute("text") or element.text or ""
                passed = str(expected) in str(actual)
                return self._result(
                    "input_value",
                    passed,
                    f"input value mismatch: expected contains {expected}, actual={actual}" if not passed else "",
                    {"expected_value": expected, "actual_value": actual},
                )
            except Exception as exc:
                return self._result("input_value", False, f"input_value check failed: {exc}")

        return self._result("input_value", False, f"input value not found in page source: {expected}")

    def assert_no_crash(self, after_state: PageState) -> dict[str, Any]:
        failures = []
        if after_state.page_source_error:
            failures.append(f"page source unavailable: {after_state.page_source_error}")
        if not after_state.page_source:
            failures.append("page source is empty")
        lower_source = (after_state.page_source or "").lower()
        for marker in CRASH_MARKERS:
            if marker.lower() in lower_source:
                failures.append(f"crash marker found: {marker}")
        if after_state.current_package and APP_PACKAGE and after_state.current_package not in {APP_PACKAGE, "io.appium.settings"}:
            if "launcher" not in after_state.current_package:
                failures.append(f"unexpected current package: {after_state.current_package}")
        return self._result("no_crash", not failures, "; ".join(failures))

    def _run_check(self, check: dict[str, Any], step: dict[str, Any], before_state: PageState, after_state: PageState) -> dict[str, Any]:
        strategy = check.get("strategy")
        payload = {**step, **check}

        if strategy == "page_signature":
            page_id = payload.get("page_id") or payload.get("expected_page") or payload.get("target_page")
            if not page_id:
                meta = self._element_meta(step.get("target"))
                page_id = meta.get("target_page") or meta.get("expected_page")
            return self.assert_page_signature(page_id, after_state) if page_id else self._result(strategy, False, "page_id is required")
        if strategy == "exists":
            return self.assert_exists(payload, after_state)
        if strategy == "not_exists":
            return self.assert_not_exists(payload, after_state)
        if strategy == "text_contains":
            return self.assert_text_contains(payload, after_state)
        if strategy == "selected":
            return self.assert_selected(payload, after_state)
        if strategy == "content_changed":
            return self.assert_content_changed(before_state, after_state)
        if strategy == "scroll_reveal":
            return self.assert_scroll_reveal(payload, before_state, after_state)
        if strategy == "dialog_opened":
            return self.assert_dialog_opened(payload, before_state, after_state)
        if strategy == "dialog_closed":
            return self.assert_dialog_closed(payload, before_state, after_state)
        if strategy == "toast":
            return self.assert_toast(payload, after_state)
        if strategy == "input_value":
            return self.assert_input_value(payload, after_state)
        if strategy == "no_crash":
            return self.assert_no_crash(after_state)
        return self._result(str(strategy), False, f"unsupported verify strategy: {strategy}")

    def _resolve_checks(self, step: dict[str, Any]) -> list[dict[str, Any]]:
        verify = step.get("verify")
        if isinstance(verify, list):
            return [item if isinstance(item, dict) else {"strategy": item} for item in verify]
        if isinstance(verify, dict):
            strategy = verify.get("strategy")
            if isinstance(strategy, list):
                return [{**verify, "strategy": item} for item in strategy]
            if isinstance(strategy, str) and "+" in strategy:
                return [{**verify, "strategy": item.strip()} for item in strategy.split("+") if item.strip()]
            return [verify]
        if isinstance(verify, str):
            return [{"strategy": verify}]
        if step.get("verify_strategy"):
            return [{"strategy": step["verify_strategy"]}]
        return self._infer_default_checks(step)

    def _infer_default_checks(self, step: dict[str, Any]) -> list[dict[str, Any]]:
        action = step.get("action")
        meta = self._element_meta(step.get("target"))
        role = step.get("role") or meta.get("role")
        effect_type = step.get("effect_type") or meta.get("effect_type")
        target_page = step.get("target_page") or meta.get("target_page")
        expected_page = step.get("expected_page") or meta.get("expected_page")

        if action in {"swipe", "swipe_point"}:
            if step.get("expected_texts") or step.get("expected_text"):
                return [{"strategy": "scroll_reveal"}]
            return [{"strategy": "content_changed"}]
        if action in {"press_back", "back"}:
            if expected_page:
                return [{"strategy": "page_signature", "page_id": expected_page}]
            return [{"strategy": "content_changed"}, {"strategy": "no_crash"}]
        if role in {"navigation_card", "menu_item"} and target_page:
            return [{"strategy": "page_signature", "page_id": target_page}]
        if role in {"tab", "filter"}:
            checks = [{"strategy": "content_changed"}, {"strategy": "no_crash"}]
            expected_signature = step.get("expected_signature") or meta.get("expected_signature")
            if expected_signature:
                checks.append({"strategy": "page_signature", "page_id": expected_signature})
            return checks
        if role == "input" or action in {"input", "input_text"}:
            return [{"strategy": "input_value"}]
        if role == "button":
            mapping = {
                "open_dialog": "dialog_opened",
                "close_dialog": "dialog_closed",
                "show_toast": "toast",
                "switch_content": "content_changed",
            }
            return [{"strategy": mapping.get(effect_type, "no_crash")}]
        return [{"strategy": "no_crash"}]

    def _collect_pages(self, case_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        pages: dict[str, dict[str, Any]] = {}
        for container in (
            case_data.get("pages"),
            case_data.get("page_signatures"),
            case_data.get("app_map", {}).get("pages") if isinstance(case_data.get("app_map"), dict) else None,
            case_data.get("component_tree", {}).get("pages") if isinstance(case_data.get("component_tree"), dict) else None,
        ):
            if isinstance(container, dict):
                for page_id, page in container.items():
                    if isinstance(page, dict):
                        data = dict(page)
                        data.setdefault("page_id", page_id)
                        pages[str(data["page_id"])] = data
            elif isinstance(container, list):
                for page in container:
                    if isinstance(page, dict) and (page.get("page_id") or page.get("id")):
                        pages[str(page.get("page_id") or page.get("id"))] = page
        return pages

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

    def _page_signature(self, page_id: str | None) -> dict[str, Any]:
        page = self.pages.get(str(page_id)) if page_id else None
        if not page:
            return {}
        return page.get("signature", page)

    def _element_meta(self, target: str | None) -> dict[str, Any]:
        if not target:
            return {}
        if target in self.elements:
            return self.elements[target]
        for element in self.elements.values():
            if target in {element.get("name"), element.get("id"), element.get("element_id")}:
                return element
        return {}

    def _element_name_present(self, element_name: str, source: str) -> bool:
        if self._contains(source, element_name):
            return True
        for element in self.elements.values():
            if element_name not in {element.get("name"), element.get("element_id"), element.get("id")}:
                continue
            locator_info = build_locator(element.get("element_id"), self.elements)
            locator = locator_info.get("locator")
            if locator and self._locator_text_in_source(locator, source):
                return True
        return False

    def _locator_text_in_source(self, locator: dict[str, Any], source: str) -> bool:
        value = str(locator.get("value", ""))
        candidates = [value]
        for marker in ("description", "descriptionContains", "text", "textContains", "resourceId"):
            for item in self._extract_uiautomator_arg(value, marker):
                candidates.append(item)
        return any(candidate and candidate in source for candidate in candidates)

    def _driver_element_exists(self, locator: dict[str, Any]) -> bool:
        if not self.driver:
            return False
        try:
            by, value = to_appium_locator(locator)
            if by == "bounds":
                return True
            return len(self.driver.find_elements(by, value)) > 0
        except (WebDriverException, Exception):
            return False

    def _expected_locator(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(payload.get("locator"), dict):
            return payload["locator"]
        target = payload.get("target")
        locator_info = build_locator(target, self.elements, payload)
        return locator_info.get("locator")

    def _expected_texts(self, payload: dict[str, Any]) -> list[str]:
        texts = payload.get("expected_texts")
        if isinstance(texts, str):
            return [texts]
        if isinstance(texts, list):
            return [str(item) for item in texts]
        for key in ("expected_text", "text", "value"):
            if payload.get(key):
                return [str(payload[key])]
        return []

    def _extract_uiautomator_arg(self, value: str, method: str) -> list[str]:
        pattern = rf'\.{method}\("((?:\\.|[^"])*)"\)'
        return [item.replace(r"\"", '"') for item in re_findall(pattern, value)]

    def _contains(self, source: str, text: Any) -> bool:
        return bool(text) and str(text) in (source or "")

    def _result(self, strategy: str, passed: bool, reason: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "strategy": strategy,
            "passed": bool(passed),
            "reason": "" if passed else reason,
            "details": extra or {},
        }


def re_findall(pattern: str, value: str) -> list[str]:
    import re

    return re.findall(pattern, value or "")
