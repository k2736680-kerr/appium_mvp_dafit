import time
from typing import Any

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.config import DEFAULT_TIMEOUT
from runner.locator import build_locator, to_appium_locator


class ActionExecutor:
    def __init__(self, driver, element_registry: Any = None):
        self.driver = driver
        self.element_registry = element_registry

    def execute(self, step: dict[str, Any]) -> dict[str, Any]:
        action = step.get("action")
        if action in {"tap", "click"}:
            return self.tap(step)
        if action in {"input", "input_text"}:
            return self.input_text(step)
        if action in {"swipe", "swipe_point"}:
            return self.swipe(step)
        if action == "drag":
            return self.drag(step)
        if action in {"press_back", "back"}:
            return self.press_back(step)
        if action == "home":
            return self.home(step)
        if action in {"wait", "sleep"}:
            return self.wait(step)
        if action in {"tap_point", "tap_coordinate"}:
            return self.tap_point(step)
        if action == "tap_ratio":
            return self.tap_ratio(step)
        raise ValueError(f"unsupported action: {action}")

    def wait_for_element(self, step: dict[str, Any]):
        locator_info = build_locator(step.get("target"), self.element_registry, step)
        locator = locator_info.get("locator")
        if not locator:
            raise ValueError(f"missing locator for step target: {step.get('target')}")

        by, value = to_appium_locator(locator)
        if by == "bounds":
            return locator_info

        timeout = float(step.get("timeout", DEFAULT_TIMEOUT))
        try:
            element = WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((by, value))
            )
        except TimeoutException as exc:
            raise AssertionError(f"element not clickable: {locator}") from exc

        locator_info["element_object"] = element
        return locator_info

    def tap(self, step: dict[str, Any]) -> dict[str, Any]:
        locator_info = self.wait_for_element(step)
        by_value = to_appium_locator(locator_info["locator"])
        if by_value[0] == "bounds":
            point = by_value[1]
            self._click_point(point["x"], point["y"])
            return {"locator_info": locator_info, "tap_mode": "bounds"}

        element = locator_info["element_object"]
        if step.get("click_mode") == "center":
            rect = element.rect
            self._click_point(int(rect["x"] + rect["width"] / 2), int(rect["y"] + rect["height"] / 2))
        else:
            element.click()
        return {"locator_info": locator_info}

    def input_text(self, step: dict[str, Any]) -> dict[str, Any]:
        locator_info = self.wait_for_element(step)
        element = locator_info["element_object"]
        element.click()
        if step.get("clear", False):
            element.clear()
        element.send_keys(step.get("text", ""))
        return {"locator_info": locator_info, "text": step.get("text", "")}

    def swipe(self, step: dict[str, Any]) -> dict[str, Any]:
        if {"start_x", "start_y", "end_x", "end_y"}.issubset(step):
            args = {
                "startX": int(step["start_x"]),
                "startY": int(step["start_y"]),
                "endX": int(step["end_x"]),
                "endY": int(step["end_y"]),
                "speed": int(step.get("speed", 2500)),
            }
        else:
            args = self._swipe_args_from_direction(step)
        self.driver.execute_script("mobile: dragGesture", args)
        return {"gesture": args}

    def drag(self, step: dict[str, Any]) -> dict[str, Any]:
        args = {
            "startX": int(step["start_x"]),
            "startY": int(step["start_y"]),
            "endX": int(step["end_x"]),
            "endY": int(step["end_y"]),
            "speed": int(step.get("speed", 2500)),
        }
        self.driver.execute_script("mobile: dragGesture", args)
        return {"gesture": args}

    def press_back(self, step: dict[str, Any]) -> dict[str, Any]:
        if hasattr(self.driver, "press_keycode"):
            self.driver.press_keycode(4)
        else:
            self.driver.back()
        return {}

    def home(self, step: dict[str, Any]) -> dict[str, Any]:
        self.driver.press_keycode(3)
        return {}

    def wait(self, step: dict[str, Any]) -> dict[str, Any]:
        seconds = float(step.get("seconds", step.get("wait_after", 1)))
        time.sleep(seconds)
        return {"seconds": seconds}

    def tap_point(self, step: dict[str, Any]) -> dict[str, Any]:
        x = int(step["x"])
        y = int(step["y"])
        self._click_point(x, y)
        return {"point": {"x": x, "y": y}}

    def tap_ratio(self, step: dict[str, Any]) -> dict[str, Any]:
        size = self.driver.get_window_size()
        x = int(size["width"] * float(step["x_ratio"]))
        y = int(size["height"] * float(step["y_ratio"]))
        self._click_point(x, y)
        return {"point": {"x": x, "y": y}}

    def _click_point(self, x: int, y: int):
        self.driver.execute_script("mobile: clickGesture", {"x": int(x), "y": int(y)})

    def _swipe_args_from_direction(self, step: dict[str, Any]) -> dict[str, int]:
        direction = step.get("direction", "up")
        size = self.driver.get_window_size()
        width = int(size["width"])
        height = int(size["height"])
        cx = width // 2
        cy = height // 2
        distance = float(step.get("distance_ratio", 0.45))
        dx = int(width * distance)
        dy = int(height * distance)

        if direction == "up":
            start_x, start_y, end_x, end_y = cx, cy + dy // 2, cx, cy - dy // 2
        elif direction == "down":
            start_x, start_y, end_x, end_y = cx, cy - dy // 2, cx, cy + dy // 2
        elif direction == "left":
            start_x, start_y, end_x, end_y = cx + dx // 2, cy, cx - dx // 2, cy
        elif direction == "right":
            start_x, start_y, end_x, end_y = cx - dx // 2, cy, cx + dx // 2, cy
        else:
            raise ValueError(f"unsupported swipe direction: {direction}")

        return {
            "startX": start_x,
            "startY": start_y,
            "endX": end_x,
            "endY": end_y,
            "speed": int(step.get("speed", 2500)),
        }
