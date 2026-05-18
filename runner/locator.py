import re
from typing import Any

from appium.webdriver.common.appiumby import AppiumBy


LOCATOR_PRIORITY = {
    "id": 10,
    "resource-id": 10,
    "resource_id": 10,
    "accessibility_id": 20,
    "accessibility id": 20,
    "content-desc": 20,
    "content_desc": 20,
    "text": 30,
    "class_text": 40,
    "class+text": 40,
    "bounds": 50,
    "xpath": 60,
    "android_uiautomator": 70,
    "class_name": 80,
    "instance": 90,
}


def normalize_by(by: str | None) -> str:
    if not by:
        return ""
    return str(by).strip().lower().replace("_", "-")


def _uiautomator_has_instance_only(value: str) -> bool:
    if "instance(" not in value:
        return False
    stable_hints = (
        ".resourceId(",
        ".description(",
        ".descriptionContains(",
        ".text(",
        ".textContains(",
    )
    return not any(hint in value for hint in stable_hints)


def rank_locator(locator: dict[str, Any] | None) -> int:
    if not locator:
        return 999

    by = normalize_by(locator.get("by"))
    value = str(locator.get("value", ""))

    if by in {"id", "resource-id", "resource_id"}:
        return LOCATOR_PRIORITY["id"]
    if by in {"accessibility-id", "accessibility id", "content-desc", "content_desc"}:
        return LOCATOR_PRIORITY["accessibility_id"]
    if by == "text":
        return LOCATOR_PRIORITY["text"]
    if by in {"class-text", "class+text"}:
        return LOCATOR_PRIORITY["class_text"]
    if by == "bounds":
        return LOCATOR_PRIORITY["bounds"]
    if by == "android-uiautomator":
        if ".resourceId(" in value:
            return LOCATOR_PRIORITY["id"]
        if ".description(" in value or ".descriptionContains(" in value:
            return LOCATOR_PRIORITY["accessibility_id"]
        if ".text(" in value or ".textContains(" in value:
            return LOCATOR_PRIORITY["text"]
        if ".className(" in value and (".text(" in value or ".textContains(" in value):
            return LOCATOR_PRIORITY["class_text"]
        if _uiautomator_has_instance_only(value):
            return LOCATOR_PRIORITY["instance"]
        return LOCATOR_PRIORITY["android_uiautomator"]
    if by == "xpath":
        return LOCATOR_PRIORITY["xpath"]
    if by == "class-name":
        return LOCATOR_PRIORITY["class_name"]
    return 500


def warn_unstable_locator(locator: dict[str, Any] | None) -> list[str]:
    if not locator:
        return ["missing_locator"]

    by = normalize_by(locator.get("by"))
    value = str(locator.get("value", ""))
    warnings = []

    if by == "android-uiautomator" and _uiautomator_has_instance_only(value):
        warnings.append("unstable_locator: android_uiautomator instance-only selector")
    if by == "class-name":
        warnings.append("unstable_locator: class_name locator may match multiple elements")
    if by == "xpath" and "instance" in value.lower():
        warnings.append("unstable_locator: xpath contains instance-like selector")
    return warnings


def select_best_locator(locators: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not locators:
        return None
    return sorted(locators, key=rank_locator)[0]


def _iter_element_candidates(element_registry: Any):
    if not element_registry:
        return
    if isinstance(element_registry, dict):
        for element_id, meta in element_registry.items():
            if isinstance(meta, dict):
                yield element_id, meta
        return
    if isinstance(element_registry, list):
        for meta in element_registry:
            if isinstance(meta, dict):
                yield meta.get("element_id") or meta.get("id") or meta.get("name"), meta


def find_element_meta(target: str | None, element_registry: Any) -> dict[str, Any]:
    if not target:
        return {}
    for element_id, meta in _iter_element_candidates(element_registry) or []:
        if target in {element_id, meta.get("element_id"), meta.get("id"), meta.get("name")}:
            result = dict(meta)
            result.setdefault("element_id", element_id)
            return result
    return {}


def build_locator(
    target: str | None = None,
    element_registry: Any = None,
    step: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step = step or {}
    element_meta = find_element_meta(target or step.get("target"), element_registry)

    candidates: list[dict[str, Any]] = []
    if isinstance(step.get("locator"), dict):
        candidates.append(step["locator"])
    if isinstance(step.get("locators"), list):
        candidates.extend(item for item in step["locators"] if isinstance(item, dict))
    if isinstance(element_meta.get("locator"), dict):
        candidates.append(element_meta["locator"])
    if isinstance(element_meta.get("locators"), list):
        candidates.extend(item for item in element_meta["locators"] if isinstance(item, dict))

    locator = select_best_locator(candidates)
    return {
        "locator": locator,
        "element": element_meta,
        "rank": rank_locator(locator),
        "warnings": warn_unstable_locator(locator),
    }


def to_appium_locator(locator: dict[str, Any]):
    by = normalize_by(locator.get("by"))
    value = locator.get("value")

    if by in {"accessibility-id", "accessibility id", "content-desc", "content_desc"}:
        return AppiumBy.ACCESSIBILITY_ID, value
    if by == "android-uiautomator":
        return AppiumBy.ANDROID_UIAUTOMATOR, value
    if by in {"id", "resource-id", "resource_id"}:
        return AppiumBy.ID, value
    if by == "xpath":
        return AppiumBy.XPATH, value
    if by == "class-name":
        return AppiumBy.CLASS_NAME, value
    if by == "text":
        escaped = str(value).replace('"', r'\"')
        return AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{escaped}")'
    if by in {"class-text", "class+text"}:
        class_name = locator.get("class") or locator.get("class_name") or "*"
        escaped = str(value).replace('"', r'\"')
        return AppiumBy.ANDROID_UIAUTOMATOR, (
            f'new UiSelector().className("{class_name}").text("{escaped}")'
        )
    if by == "bounds":
        bounds = str(value)
        match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
        if match:
            x1, y1, x2, y2 = [int(item) for item in match.groups()]
            return "bounds", {"x": int((x1 + x2) / 2), "y": int((y1 + y2) / 2)}

    raise ValueError(f"unsupported locator type: {locator.get('by')}")
