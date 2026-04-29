import sys
import time
import threading

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from core.artifacts import ArtifactManager
from core.audio import AudioInjector
from core.config import APP_PACKAGE, DEFAULT_TIMEOUT, PROJECT_ROOT
from core.locators import to_appium_locator
from core.translation import (
    AliyunTranslationValidator,
    extract_visible_texts,
    pick_translation_pair,
)


def safe_print(*args, **kwargs):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe_args = [
        str(arg).encode(encoding, errors="backslashreplace").decode(encoding)
        for arg in args
    ]
    print(*safe_args, **kwargs)


DEFAULT_BLOCKERS = [
    "耳机未连接",
    "请连接蓝牙耳机以使用此功能",
    "无录音权限",
    "录音权限",
    "麦克风权限",
    "连接错误",
    "解析错误",
]

ROLE_POINTS = {
    # 1080x2400 下 Inspector 麦克风 bounds 约 [435,2043][645,2253]
    # 换算成比例，避免以后不同分辨率完全失效。
    "bottom_mic": {"x_ratio": 0.50, "y_ratio": 0.895},
    "dual_earbuds_mic": {"x_ratio": 0.50, "y_ratio": 0.895},
    "phone_headset_mic": {"x_ratio": 0.50, "y_ratio": 0.895},
    "bottom_center_mic": {"x_ratio": 0.50, "y_ratio": 0.895},
}


class CaseRunner:
    def __init__(self, driver, case_data):
        self.driver = driver
        self.case = case_data
        self.case_id = case_data.get("case_id", "unnamed_case")
        self.artifacts = ArtifactManager(self.case_id)
        self.audio = AudioInjector(PROJECT_ROOT)
        self.translation = AliyunTranslationValidator()
        self.blockers = case_data.get("blockers", DEFAULT_BLOCKERS)
        self.context = {"marked_texts": []}

    def setup_start_state(self):
        start = self.case.get("start", {})

        if start.get("terminate_app", False):
            self.driver.terminate_app(APP_PACKAGE)
            time.sleep(start.get("wait_after_terminate", 1))

        if start.get("press_home", False):
            self.driver.press_keycode(3)
            time.sleep(start.get("wait_after_home", 1))

        if start.get("activate_app", False):
            self.driver.activate_app(APP_PACKAGE)
            time.sleep(start.get("wait_after_activate", 2))

        self.artifacts.save(self.driver, 0, "start_state")

    def run(self):
        self.setup_start_state()
        steps = self.case.get("steps", [])
        for index, step in enumerate(steps, start=1):
            self.run_step(index, step)

    def step_name(self, index, step):
        return step.get("name") or step.get("action") or f"step_{index}"

    def run_step(self, index, step):
        action = step.get("action")
        name = self.step_name(index, step)
        safe_print(f"\n[STEP] {index}: {name} | action={action}")

        if action == "click":
            self.action_click(step)
        elif action == "input_text":
            self.action_input_text(step)
        elif action == "tap_role":
            self.action_tap_role(step)
        elif action == "tap_my_account":
            self.action_tap_my_account(step)
        elif action == "tap_ratio":
            self.action_tap_ratio(step)
        elif action in ("tap_point", "tap_coordinate"):
            self.action_tap_point(step)
        elif action == "swipe_point":
            self.action_swipe_point(step)
        elif action == "long_press_role":
            self.action_long_press_role(step)
        elif action == "long_press_point":
            self.action_long_press_point(step)
        elif action == "assert_text":
            self.action_assert_text(step)
        elif action == "assert_not_text":
            self.action_assert_not_text(step)
        elif action == "sleep":
            seconds = float(step.get("seconds", 1))
            print(f"[SLEEP] {seconds}s")
            time.sleep(seconds)
        elif action == "play_audio":
            self.audio.play(
                step["file"],
                wait_after=step.get("wait_after", 1),
                fallback_file=step.get("fallback_file"),
            )
        elif action == "mark_page_texts":
            self.action_mark_page_texts()
        elif action == "wait_text":
            self.action_wait_text(step)
        elif action == "assert_translation":
            self.action_assert_translation(step)
        elif action == "press_back":
            self.driver.press_keycode(4)
            time.sleep(step.get("wait_after", 1))
        elif action == "home":
            self.driver.press_keycode(3)
            time.sleep(step.get("wait_after", 1))
        elif action == "dump_page":
            pass
        else:
            raise ValueError(f"不支持的 action: {action}")

        self.artifacts.save(self.driver, index, name)

        if step.get("check_blockers", True):
            self.fail_if_blocked(index, name)

    def fail_if_blocked(self, index, name):
        page_source = self.driver.page_source
        for keyword in self.blockers:
            # 注意：不要把双耳机页右上角的“未连接”放进 blockers。
            # “耳机未连接”弹窗才是阻断。
            if keyword and keyword in page_source:
                self.artifacts.save(self.driver, index, f"blocked_{name}")
                raise AssertionError(
                    f"用例失败：步骤【{name}】后检测到阻断信息：{keyword}"
                )

    def wait_for_element(self, locator, timeout=None):
        by, value = to_appium_locator(locator)
        timeout = timeout or DEFAULT_TIMEOUT
        return WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, value))
        )

    def action_click(self, step):
        locator = step.get("locator")
        if not locator:
            raise ValueError("click 步骤缺少 locator")

        timeout = step.get("timeout", DEFAULT_TIMEOUT)
        try:
            el = self.wait_for_element(locator, timeout=timeout)
        except TimeoutException:
            raise AssertionError(f"找不到或无法点击元素: {locator}")

        click_mode = step.get("click_mode", "element")
        if click_mode == "center":
            self.click_element_center(el)
        else:
            el.click()

        time.sleep(step.get("wait_after", 1))

    def action_input_text(self, step):
        locator = step.get("locator")
        text = step.get("text", "")
        if not locator:
            raise ValueError("input_text 步骤缺少 locator")

        timeout = step.get("timeout", DEFAULT_TIMEOUT)
        try:
            el = self.wait_for_element(locator, timeout=timeout)
        except TimeoutException:
            raise AssertionError(f"找不到输入框元素: {locator}")

        el.click()
        if step.get("clear", False):
            el.clear()
        el.send_keys(text)
        time.sleep(step.get("wait_after", 1))

    def click_element_center(self, el):
        rect = el.rect
        x = int(rect["x"] + rect["width"] / 2)
        y = int(rect["y"] + rect["height"] / 2)
        self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})

    def action_tap_role(self, step):
        role = step.get("role")
        if role not in ROLE_POINTS:
            raise ValueError(f"未知 role: {role}，可用 role: {list(ROLE_POINTS)}")
        point = ROLE_POINTS[role]
        self.tap_ratio(point["x_ratio"], point["y_ratio"])
        time.sleep(step.get("wait_after", 1))

    def action_tap_ratio(self, step):
        self.tap_ratio(float(step["x_ratio"]), float(step["y_ratio"]))
        time.sleep(step.get("wait_after", 1))

    def action_tap_point(self, step):
        self.driver.execute_script(
            "mobile: clickGesture",
            {"x": int(step["x"]), "y": int(step["y"])},
        )
        time.sleep(step.get("wait_after", 1))

    def action_tap_my_account(self, step):
        def is_account_page():
            source = self.driver.page_source
            return "用户名" in source and "生日" in source and "性别" in source

        if is_account_page():
            print("[TAP_MY_ACCOUNT] 已在我的账户页，跳过入口点击")
            time.sleep(step.get("wait_after", 1))
            return

        attempts = int(step.get("attempts", 4))
        for attempt in range(1, attempts + 1):
            print(f"[TAP_MY_ACCOUNT] 点击入口，第 {attempt}/{attempts} 次")
            self.driver.execute_script(
                "mobile: clickGesture",
                {"x": int(step["x"]), "y": int(step["y"])},
            )
            time.sleep(1)
            if is_account_page():
                break

        time.sleep(step.get("wait_after", 1))

    def action_swipe_point(self, step):
        start_x = int(step["start_x"])
        start_y = int(step["start_y"])
        end_x = int(step["end_x"])
        end_y = int(step["end_y"])
        speed = int(step.get("speed", 2500))
        print(
            "[SWIPE]",
            f"start=({start_x},{start_y})",
            f"end=({end_x},{end_y})",
            f"speed={speed}",
        )
        self.driver.execute_script(
            "mobile: dragGesture",
            {
                "startX": start_x,
                "startY": start_y,
                "endX": end_x,
                "endY": end_y,
                "speed": speed,
            },
        )
        time.sleep(step.get("wait_after", 1))

    def action_long_press_role(self, step):
        role = step.get("role")
        if role not in ROLE_POINTS:
            raise ValueError(f"未知 role: {role}，可用 role: {list(ROLE_POINTS)}")
        point = ROLE_POINTS[role]
        x, y = self.point_from_ratio(point["x_ratio"], point["y_ratio"])
        self.long_press_point(x, y, step)

    def action_long_press_point(self, step):
        self.long_press_point(int(step["x"]), int(step["y"]), step)

    def long_press_point(self, x, y, step):
        audio_file = step.get("audio_file") or step.get("file")
        fallback_file = step.get("fallback_file")
        duration = step.get("duration", "auto")
        audio_start_delay_ms = int(step.get("audio_start_delay_ms", 300))
        release_padding_ms = int(step.get("release_padding_ms", 700))
        audio_duration_ms = None
        resolved_audio_file = None

        if audio_file:
            resolved_audio_file = self.audio.resolve_with_fallback(
                audio_file,
                fallback_file=fallback_file,
            )
            audio_duration_ms = self.audio.duration_ms(resolved_audio_file)

        if duration in (None, "", "auto"):
            duration_ms = 5000
            if audio_duration_ms:
                duration_ms = audio_duration_ms + audio_start_delay_ms + release_padding_ms
        else:
            duration_ms = int(duration)
            if audio_duration_ms:
                duration_ms = max(
                    duration_ms,
                    audio_duration_ms + audio_start_delay_ms + release_padding_ms,
                )

        print(f"[LONG_PRESS] x={x}, y={y}, duration={duration_ms}ms")

        audio_error = []
        audio_thread = None

        if resolved_audio_file:
            def play_during_press():
                try:
                    time.sleep(audio_start_delay_ms / 1000)
                    self.audio.play(resolved_audio_file, wait_after=0)
                except Exception as exc:
                    audio_error.append(exc)

            print(
                "[LONG_PRESS_AUDIO]",
                f"file={resolved_audio_file}",
                f"delay={audio_start_delay_ms}ms",
                f"duration={audio_duration_ms}ms",
            )
            audio_thread = threading.Thread(target=play_during_press, daemon=True)
            audio_thread.start()

        self.driver.execute_script(
            "mobile: longClickGesture",
            {"x": int(x), "y": int(y), "duration": int(duration_ms)},
        )

        if audio_thread:
            join_timeout = ((audio_duration_ms or duration_ms) + 5000) / 1000
            audio_thread.join(timeout=join_timeout)
            if audio_thread.is_alive():
                raise TimeoutError(f"长按期间音频播放未在 {join_timeout:.1f}s 内结束")
            if audio_error:
                raise RuntimeError(f"长按期间音频播放失败: {audio_error[0]}")

        time.sleep(step.get("wait_after", 1))

    def tap_ratio(self, x_ratio, y_ratio):
        x, y = self.point_from_ratio(x_ratio, y_ratio)
        print(f"[TAP_RATIO] x={x}, y={y}, ratio=({x_ratio}, {y_ratio})")
        self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})

    def point_from_ratio(self, x_ratio, y_ratio):
        size = self.driver.get_window_size()
        x = int(size["width"] * x_ratio)
        y = int(size["height"] * y_ratio)
        return x, y

    def action_assert_text(self, step):
        text = step.get("text")
        timeout = step.get("timeout", DEFAULT_TIMEOUT)
        if not text:
            raise ValueError("assert_text 缺少 text")
        try:
            WebDriverWait(self.driver, timeout).until(lambda d: text in d.page_source)
        except TimeoutException:
            raise AssertionError(f"等待文本失败: {text}")

    def action_assert_not_text(self, step):
        text = step.get("text")
        if not text:
            raise ValueError("assert_not_text 缺少 text")
        if text in self.driver.page_source:
            raise AssertionError(f"页面不应出现文本，但实际出现了: {text}")

    def action_wait_text(self, step):
        self.action_assert_text(step)

    def action_mark_page_texts(self):
        page_texts = self.extract_page_texts()
        self.context["marked_texts"] = page_texts
        print(f"[TEXTS] marked {len(page_texts)} baseline texts")

    def extract_page_texts(self):
        return extract_visible_texts(self.driver.page_source)

    def action_assert_translation(self, step):
        timeout = float(step.get("timeout", DEFAULT_TIMEOUT))
        poll_interval = float(step.get("poll_interval", 2))
        source_lang = step.get("source_lang")
        target_lang = step.get("target_lang")

        if not source_lang or not target_lang:
            raise ValueError("assert_translation 缺少 source_lang 或 target_lang")

        baseline_texts = self.context.get("marked_texts", [])
        end_time = time.time() + timeout
        last_source = None
        last_target = None
        last_texts = []

        while time.time() < end_time:
            page_texts = self.extract_page_texts()
            last_texts = page_texts
            source_text, target_text = pick_translation_pair(
                page_texts,
                baseline_texts,
                source_lang,
                target_lang,
            )
            last_source = source_text
            last_target = target_text

            if source_text and target_text:
                result = self.translation.validate_translation(
                    source_text=source_text,
                    target_text=target_text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                safe_print(
                    "[TRANSLATION]",
                    f"source={source_text}",
                    f"target={target_text}",
                    f"back_translation={result['back_translation']}",
                    f"pass={result['pass']}",
                )
                if result["pass"]:
                    return
                raise AssertionError(
                    "翻译语义校验失败："
                    f"source={source_text} | "
                    f"target={target_text} | "
                    f"back_translation={result['back_translation']} | "
                    f"reason={result['reason'] or '模型判定不一致'}"
                )

            time.sleep(poll_interval)

        raise AssertionError(
            "等待翻译结果超时："
            f"source_lang={source_lang}, target_lang={target_lang}, "
            f"last_source={last_source}, last_target={last_target}, "
            f"visible_texts={last_texts}"
        )
