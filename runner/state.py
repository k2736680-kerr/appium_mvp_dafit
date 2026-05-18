import base64
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PageState:
    page_source: str = ""
    page_source_error: str = ""
    screenshot_png: bytes | None = None
    screenshot_error: str = ""
    visible_texts: list[str] = field(default_factory=list)
    source_hash: str = ""
    captured_at: float = 0.0
    current_package: str = ""
    current_activity: str = ""

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "page_source_error": self.page_source_error,
            "screenshot_error": self.screenshot_error,
            "visible_texts": self.visible_texts,
            "source_hash": self.source_hash,
            "captured_at": self.captured_at,
            "current_package": self.current_package,
            "current_activity": self.current_activity,
        }


def get_page_source(driver) -> tuple[str, str]:
    try:
        return driver.page_source or "", ""
    except Exception as exc:  # pragma: no cover - exercised by real Appium failures
        return "", str(exc)


def get_screenshot(driver) -> tuple[bytes | None, str]:
    try:
        if hasattr(driver, "get_screenshot_as_png"):
            return driver.get_screenshot_as_png(), ""
        if hasattr(driver, "get_screenshot_as_base64"):
            return base64.b64decode(driver.get_screenshot_as_base64()), ""
    except Exception as exc:  # pragma: no cover - exercised by real Appium failures
        return None, str(exc)
    return None, "driver does not expose screenshot APIs"


def page_source_hash(page_source: str) -> str:
    return hashlib.sha256((page_source or "").encode("utf-8", errors="replace")).hexdigest()


def get_visible_texts(page_source: str) -> list[str]:
    if not page_source:
        return []

    texts: list[str] = []
    try:
        root = ET.fromstring(page_source)
        for node in root.iter():
            for attr in ("text", "content-desc", "label", "name"):
                value = (node.attrib.get(attr) or "").strip()
                if value:
                    texts.append(value)
    except ET.ParseError:
        patterns = [
            r'\btext="([^"]+)"',
            r'\bcontent-desc="([^"]+)"',
            r"\btext='([^']+)'",
            r"\bcontent-desc='([^']+)'",
        ]
        for pattern in patterns:
            texts.extend(value.strip() for value in re.findall(pattern, page_source) if value.strip())

    deduped = []
    seen = set()
    for text in texts:
        if text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _safe_driver_attr(driver, attr: str) -> str:
    try:
        value = getattr(driver, attr)
        if callable(value):
            value = value()
        return str(value or "")
    except Exception:
        return ""


def capture_state(driver) -> PageState:
    source, source_error = get_page_source(driver)
    screenshot, screenshot_error = get_screenshot(driver)
    return PageState(
        page_source=source,
        page_source_error=source_error,
        screenshot_png=screenshot,
        screenshot_error=screenshot_error,
        visible_texts=get_visible_texts(source),
        source_hash=page_source_hash(source),
        captured_at=time.time(),
        current_package=_safe_driver_attr(driver, "current_package"),
        current_activity=_safe_driver_attr(driver, "current_activity"),
    )
