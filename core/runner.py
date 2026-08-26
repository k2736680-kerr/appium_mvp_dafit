import sys
import time
import threading
import re

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from core.artifacts import ArtifactManager
from core.audio import AudioInjector
from core.config import (
    APP_PACKAGE,
    AURO_AUTO_LOGIN,
    AURO_LOGIN_ACCOUNT,
    AURO_LOGIN_PASSWORD,
    DEFAULT_TIMEOUT,
    PROJECT_ROOT,
)
from core.locators import to_appium_locator
from core.translation import (
    AliyunTranslationValidator,
    AliyunImageTranslationValidator,
    extract_visible_texts,
    text_matches_language,
    resolve_translation_pair,
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
    "图片翻译失败",
    "拍照翻译失败",
    "翻译失败",
    "翻译出错",
    "图片识别失败",
    "识别失败",
    "识别错误",
    "请求失败",
    "网络异常",
    "服务异常",
    "发生错误",
    "出错了",
    "请稍后重试",
    "无法识别",
    "未能识别",
    "无法翻译",
]

ROLE_POINTS = {
    # 1080x2400 下 Inspector 麦克风 bounds 约 [435,2043][645,2253]。
    # 换算成比例，避免以后不同分辨率完全失效。
    "bottom_mic": {"x_ratio": 0.50, "y_ratio": 0.895},
    "dual_earbuds_mic": {"x_ratio": 0.50, "y_ratio": 0.895},
    "phone_headset_mic": {"x_ratio": 0.50, "y_ratio": 0.895},
    "bottom_center_mic": {"x_ratio": 0.50, "y_ratio": 0.895},
}

MIC_POINT_RESOLVE_TIMEOUT = 5


class CaseRunner:
    def __init__(self, driver, case_data):
        self.driver = driver
        self.case = case_data
        self.case_id = case_data.get("case_id", "unnamed_case")
        self.artifacts = ArtifactManager(self.case_id)
        self.audio = AudioInjector(PROJECT_ROOT)
        self.translation = AliyunTranslationValidator()
        self.image_translation = AliyunImageTranslationValidator()
        self.blockers = case_data.get("blockers", DEFAULT_BLOCKERS)
        self.context = {"marked_texts": [], "auto_login_attempted": False}

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
        if any(
            step.get("action") in {"play_audio", "long_press_role"}
            for step in steps
        ):
            safe_print("[AUDIO] resetting emulator host microphone bridge")
            self.audio.prepare()
        for index, step in enumerate(steps, start=1):
            self.run_step(index, step)

    def step_name(self, index, step):
        return step.get("name") or step.get("action") or f"step_{index}"

    def run_step(self, index, step):
        action = step.get("action")
        name = self.step_name(index, step)
        safe_print(f"\n[STEP] {index}: {name} | action={action}")

        # 新版单向模式会在首次录音前弹出“请保持屏幕开启”提示。
        # 它不是业务失败；若不关闭，后面的录音和语言选择都会在弹窗下执行。
        self.dismiss_known_transient_dialogs()

        if action == "click":
            self.action_click(step)
        elif action == "input_text":
            self.action_input_text(step)
        elif action == "tap_role":
            self.action_tap_role(step)
        elif action == "tap_my_account":
            self.action_tap_my_account(step)
        elif action == "tap_first_album_photo":
            self.action_tap_first_album_photo(step)
        elif action == "tap_text_center":
            self.action_tap_text_center(step)
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
        elif action == "hide_keyboard":
            self.hide_keyboard_if_present()
            time.sleep(step.get("wait_after", 1))
        elif action == "play_audio":
            wait_for_text = str(step.get("wait_for_text") or "").strip()
            if wait_for_text:
                timeout = float(step.get("wait_for_text_timeout", DEFAULT_TIMEOUT))
                safe_print("[AUDIO]", f"waiting for app state: {wait_for_text}")
                try:
                    WebDriverWait(self.driver, timeout).until(
                        lambda d: wait_for_text in d.page_source
                    )
                except TimeoutException:
                    raise AssertionError(
                        f"音频播放前等待 App 状态超时：{wait_for_text}"
                    )
            pre_delay = float(step.get("pre_delay", 0) or 0)
            if pre_delay > 0:
                print(f"[AUDIO] pre_delay {pre_delay}s")
                time.sleep(pre_delay)
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
        elif action == "assert_image_translation":
            self.action_assert_image_translation(step)
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

        self.dismiss_known_transient_dialogs()

        self.artifacts.save(self.driver, index, name)

        if step.get("check_blockers", True):
            self.fail_if_blocked(index, name)

    def dismiss_known_transient_dialogs(self):
        """Dismiss only the known non-business prompt introduced by the app.

        Do not click generic confirmation buttons unless the prompt text is present:
        profile and picker flows legitimately use those buttons as business actions.
        """
        try:
            page_source = self.driver.page_source
        except Exception:
            return False

        if "\u8bf7\u4fdd\u6301\u5c4f\u5e55\u5f00\u542f" not in page_source:
            return False

        for value in ("\u6211\u77e5\u9053\u4e86", "\u77e5\u9053\u4e86", "OK"):
            try:
                el = self.optional_element(
                    {"by": "accessibility_id", "value": value}, timeout=1
                )
                if el:
                    self.click_element_center(el)
                    safe_print("[DISMISS_PROMPT]", f"clicked={value}")
                    time.sleep(0.5)
                    return True
            except Exception:
                continue
        safe_print("[DISMISS_PROMPT]", "screen-on prompt present but no dismiss button found")
        return False

    def fail_if_blocked(self, index, name):
        page_source = self.driver.page_source
        keyword = self.find_blocker(page_source)
        if keyword:
            self.artifacts.save(self.driver, index, f"blocked_{name}")
            raise AssertionError(
                f"用例失败：步骤【{name}】后检测到阻断信息：{keyword}"
            )

    def find_blocker(self, page_source):
        for keyword in self.blockers:
            # 注意：不要把双耳机页右上角的“未连接”放进 blockers。
            # “耳机未连接”弹窗才是阻断。
            if keyword and keyword in page_source:
                return keyword
        return None

    def page_has_all(self, page_source, *texts):
        return all(text in page_source for text in texts)

    def is_account_page(self, page_source):
        if "我的 Auro" in page_source:
            return False
        return "用户名" in page_source and "生日" in page_source and "性别" in page_source

    def is_login_required(self, page_source):
        return self.page_has_all(page_source, "需要登录", "请登录")

    def is_login_entry_page(self, page_source):
        return "点击登录" in page_source

    def is_login_form_page(self, page_source):
        return (
            "android.widget.EditText" in page_source
            and "登录" in page_source
        )

    def is_login_page(self, page_source):
        return self.is_login_entry_page(page_source) or self.is_login_form_page(page_source)

    def is_dual_earbuds_disconnected(self, page_source):
        return self.page_has_all(page_source, "双耳机模式", "未连接")

    def is_logged_in(self, page_source):
        if self.is_account_page(page_source):
            return True
        return "我的账户" in page_source and "点击登录" not in page_source

    def optional_element(self, locator, timeout=2):
        try:
            return self.wait_for_element(locator, timeout=timeout)
        except TimeoutException:
            return None

    def click_optional(self, locators, timeout=2):
        for locator in locators:
            el = self.optional_element(locator, timeout=timeout)
            if not el:
                continue
            try:
                el.click()
            except Exception:
                self.click_element_center(el)
            return True
        return False

    def wait_for_element(self, locator, timeout=None):
        by, value = to_appium_locator(locator)
        timeout = timeout or DEFAULT_TIMEOUT
        return WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, value))
        )

    def click_element_for_step(self, el, step):
        click_mode = step.get("click_mode", "element")
        if click_mode == "center":
            self.click_element_center(el)
        else:
            el.click()

    def action_click(self, step):
        locator = step.get("locator")
        if not locator:
            raise ValueError("click 步骤缺少 locator")

        timeout = step.get("timeout", DEFAULT_TIMEOUT)
        fallback_locators = list(step.get("fallback_locators", []))
        # Android system camera / picker controls differ by image provider and
        # locale.  Keep the recorded label first, then try the equivalent action.
        if locator.get("by") == "accessibility_id":
            value = locator.get("value")
            if value == "完成":
                fallback_locators.extend(
                    {"by": "accessibility_id", "value": label}
                    for label in ("使用照片", "Use photo", "Done")
                )
            elif value == "提交":
                fallback_locators.extend(
                    {"by": "accessibility_id", "value": label}
                    for label in ("保存", "Save", "确认")
                )
        for attempt in range(2):
            try:
                el = self.wait_for_element(locator, timeout=timeout)
            except TimeoutException:
                if attempt == 0 and step.get("hide_keyboard_on_retry", True):
                    self.hide_keyboard_if_present()
                    continue
                for fallback_locator in fallback_locators:
                    try:
                        el = self.wait_for_element(
                            fallback_locator,
                            timeout=step.get("fallback_timeout", 3),
                        )
                    except TimeoutException:
                        continue
                    print(
                        "[CLICK_FALLBACK]",
                        f"locator not found: {locator};",
                        f"use fallback locator: {fallback_locator}",
                    )
                    self.click_element_for_step(el, step)
                    time.sleep(step.get("wait_after", 1))
                    return
                if step.get("fallback_tap"):
                    fallback = step["fallback_tap"]
                    print(
                        "[CLICK_FALLBACK]",
                        f"locator not found: {locator};",
                        f"tap ({fallback['x']},{fallback['y']})",
                    )
                    self.driver.execute_script(
                        "mobile: clickGesture",
                        {"x": int(fallback["x"]), "y": int(fallback["y"])},
                    )
                    time.sleep(step.get("wait_after", 1))
                    return
                if step.get("optional"):
                    print("[CLICK_OPTIONAL]", f"locator not found, skip: {locator}")
                    time.sleep(step.get("wait_after_missing", 0))
                    return
                raise AssertionError(f"找不到或无法点击元素: {locator}")

            self.click_element_for_step(el, step)
            time.sleep(step.get("wait_after", 1))
            if attempt == 0 and self.is_login_required(self.driver.page_source):
                self.ensure_logged_in("click")
                continue
            return

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
        if step.get("hide_keyboard_after", True):
            self.hide_keyboard_if_present()
        time.sleep(step.get("wait_after", 1))

    def click_element_center(self, el):
        rect = el.rect
        x = int(rect["x"] + rect["width"] / 2)
        y = int(rect["y"] + rect["height"] / 2)
        self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})

    def is_keyboard_shown(self):
        checker = getattr(self.driver, "is_keyboard_shown", None)
        if not checker:
            return None
        try:
            return bool(checker())
        except Exception:
            return None

    def hide_keyboard_if_present(self):
        shown_before = self.is_keyboard_shown()
        if shown_before is False:
            return False

        attempted = False
        try:
            self.driver.hide_keyboard()
            attempted = True
            time.sleep(0.5)
        except Exception:
            pass

        if self.is_keyboard_shown() is True:
            try:
                self.driver.press_keycode(4)
                attempted = True
                time.sleep(0.5)
            except Exception:
                pass
        return attempted

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

    def action_tap_first_album_photo(self, step):
        timeout = float(step.get("timeout", DEFAULT_TIMEOUT))
        prefixes = step.get("description_contains") or ["照片拍摄于", "Photo taken"]
        end_time = time.time() + timeout
        last_error = None

        # 测试图片固定在相册的“四月”分组。夜跑期间 App 会自动保存
        # 截图/图片到最新月份，因此不能再依赖首图或 Inspector 的 instance。
        # 先按媒体日期滚动定位；找不到时直接失败，避免选错图后制造假业务失败。
        album_month_text = step.get("album_month_text")
        if album_month_text:
            locator = {
                "by": "android_uiautomator",
                "value": f'new UiSelector().text("{album_month_text}")',
            }
            by, value = to_appium_locator(locator)
            max_scrolls = int(step.get("max_scrolls", 8))
            for scroll_index in range(max_scrolls + 1):
                try:
                    elements = self.driver.find_elements(by, value)
                except Exception as exc:
                    last_error = exc
                    elements = []
                if elements:
                    rect = elements[0].rect
                    x = int(step.get("album_photo_x", 179))
                    y = int(rect["y"] + rect["height"] + step.get("album_photo_offset_y", 180))
                    safe_print(
                        "[ALBUM_PHOTO_MONTH]",
                        f"month={album_month_text}",
                        f"scrolls={scroll_index}",
                        f"header={rect}",
                        f"tap=({x},{y})",
                    )
                    self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
                    time.sleep(step.get("wait_after", 1))
                    return
                if scroll_index < max_scrolls:
                    self.driver.execute_script(
                        "mobile: dragGesture",
                        {"startX": 540, "startY": 2050, "endX": 540, "endY": 1200, "speed": 2500},
                    )
                    time.sleep(0.7)
            raise AssertionError(
                f"找不到相册测试图片分组 {album_month_text}；"
                f"已滚动 {max_scrolls} 次；last_error={last_error}"
            )

        album_month_text = step.get("album_month_text")
        if album_month_text:
            locator = {
                "by": "android_uiautomator",
                "value": f'new UiSelector().text("{album_month_text}")',
            }
            by, value = to_appium_locator(locator)
            max_scrolls = int(step.get("max_scrolls", 8))
            for scroll_index in range(max_scrolls + 1):
                try:
                    elements = self.driver.find_elements(by, value)
                except Exception as exc:
                    last_error = exc
                    elements = []
                if elements:
                    rect = elements[0].rect
                    x = int(step.get("album_photo_x", 179))
                    y = int(rect["y"] + rect["height"] + step.get("album_photo_offset_y", 180))
                    safe_print(
                        "[ALBUM_PHOTO_MONTH]",
                        f"month={album_month_text}",
                        f"scrolls={scroll_index}",
                        f"header={rect}",
                        f"tap=({x},{y})",
                    )
                    self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
                    time.sleep(step.get("wait_after", 1))
                    return
                if scroll_index < max_scrolls:
                    self.driver.execute_script(
                        "mobile: dragGesture",
                        {"startX": 540, "startY": 2050, "endX": 540, "endY": 1200, "speed": 2500},
                    )
                    time.sleep(0.7)
            raise AssertionError(
                f"找不到相册测试图片分组 {album_month_text}；"
                f"已滚动 {max_scrolls} 次；last_error={last_error}"
            )

        photo_date_contains = step.get("photo_date_contains")
        if photo_date_contains:
            locator = {
                "by": "android_uiautomator",
                "value": f'new UiSelector().descriptionContains("{photo_date_contains}")',
            }
            by, value = to_appium_locator(locator)
            max_scrolls = int(step.get("max_scrolls", 8))
            for scroll_index in range(max_scrolls + 1):
                try:
                    elements = self.driver.find_elements(by, value)
                except Exception as exc:
                    last_error = exc
                    elements = []
                if elements:
                    rect = elements[0].rect
                    x = int(rect["x"] + rect["width"] / 2)
                    y = int(rect["y"] + rect["height"] / 2)
                    safe_print(
                        "[ALBUM_PHOTO_DATE]",
                        f"date={photo_date_contains}",
                        f"scrolls={scroll_index}",
                        f"rect={rect}",
                        f"tap=({x},{y})",
                    )
                    self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
                    time.sleep(step.get("wait_after", 1))
                    return
                if scroll_index < max_scrolls:
                    self.driver.execute_script(
                        "mobile: dragGesture",
                        {
                            "startX": 540,
                            "startY": 2050,
                            "endX": 540,
                            "endY": 1200,
                            "speed": 2500,
                        },
                    )
                    time.sleep(0.7)
            raise AssertionError(
                f"找不到相册测试图片（日期包含 {photo_date_contains}），"
                f"已滚动 {max_scrolls} 次；last_error={last_error}"
            )

        # Inspector originally selected a concrete view instance.  The old
        # implementation discarded that information and always chose the first
        # thumbnail, so a newly-added gallery image silently changed all image
        # translation cases.  Prefer the recorded target when it is available.
        view_instance = step.get("view_instance")
        if view_instance is not None:
            locator = {
                "by": "android_uiautomator",
                "value": (
                    'new UiSelector().className("android.view.View")'
                    f".instance({int(view_instance)})"
                ),
            }
            try:
                el = self.wait_for_element(locator, timeout=timeout)
                rect = el.rect
                x = int(rect["x"] + rect["width"] / 2)
                y = int(rect["y"] + rect["height"] / 2)
                safe_print(
                    "[ALBUM_PHOTO_RECORDED]",
                    f"instance={view_instance}",
                    f"rect={rect}",
                    f"tap=({x},{y})",
                )
                self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
                time.sleep(step.get("wait_after", 1))
                return
            except Exception as exc:
                last_error = exc

        while time.time() < end_time:
            for prefix in prefixes:
                locator = {
                    "by": "android_uiautomator",
                    "value": f'new UiSelector().descriptionContains("{prefix}")',
                }
                by, value = to_appium_locator(locator)
                try:
                    elements = self.driver.find_elements(by, value)
                except Exception as exc:
                    last_error = exc
                    elements = []
                if not elements:
                    continue
                el = elements[0]
                rect = el.rect
                x = int(rect["x"] + rect["width"] / 2)
                y = int(rect["y"] + rect["height"] / 2)
                print(
                    "[ALBUM_PHOTO]",
                    f"descriptionContains={prefix}",
                    f"rect={rect}",
                    f"tap=({x},{y})",
                )
                self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
                time.sleep(step.get("wait_after", 1))
                return
            time.sleep(0.5)

        page_source = self.driver.page_source
        match = re.search(
            r'content-desc="[^"]*(?:照片拍摄于|Photo taken)[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            page_source,
        )
        if match:
            left, top, right, bottom = [int(value) for value in match.groups()]
            x = int((left + right) / 2)
            y = int((top + bottom) / 2)
            print("[ALBUM_PHOTO_XML]", f"bounds=({left},{top},{right},{bottom})", f"tap=({x},{y})")
            self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
            time.sleep(step.get("wait_after", 1))
            return

        fallback = step.get("fallback_tap")
        if fallback:
            print(
                "[ALBUM_PHOTO_FALLBACK]",
                f"tap ({fallback['x']},{fallback['y']})",
                f"last_error={last_error}",
            )
            self.driver.execute_script(
                "mobile: clickGesture",
                {"x": int(fallback["x"]), "y": int(fallback["y"])},
            )
            time.sleep(step.get("wait_after", 1))
            return

        raise AssertionError("找不到 Google 相册第一张图片缩略图")

    def action_tap_text_center(self, step):
        texts = step.get("texts") or [step.get("text")]
        texts = [text for text in texts if text]
        if not texts:
            raise ValueError("tap_text_center 缺少 text/texts")

        timeout = float(step.get("timeout", DEFAULT_TIMEOUT))
        end_time = time.time() + timeout
        last_error = None

        while time.time() < end_time:
            for text in texts:
                for selector in (
                    f'new UiSelector().text("{text}")',
                    f'new UiSelector().descriptionContains("{text}")',
                ):
                    locator = {"by": "android_uiautomator", "value": selector}
                    by, value = to_appium_locator(locator)
                    try:
                        elements = self.driver.find_elements(by, value)
                    except Exception as exc:
                        last_error = exc
                        elements = []
                    if not elements:
                        continue
                    el = elements[0]
                    rect = el.rect
                    x = int(rect["x"] + rect["width"] / 2)
                    y = int(rect["y"] + rect["height"] / 2)
                    print("[TAP_TEXT_CENTER]", f"text={text}", f"rect={rect}", f"tap=({x},{y})")
                    self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
                    time.sleep(step.get("wait_after", 1))
                    return
            time.sleep(0.5)

        fallback = step.get("fallback_tap")
        if fallback:
            print(
                "[TAP_TEXT_CENTER_FALLBACK]",
                f"tap ({fallback['x']},{fallback['y']})",
                f"texts={texts}",
                f"last_error={last_error}",
            )
            self.driver.execute_script(
                "mobile: clickGesture",
                {"x": int(fallback["x"]), "y": int(fallback["y"])},
            )
            time.sleep(step.get("wait_after", 1))
            return

        raise AssertionError(f"找不到文本元素: {texts}")

    def action_tap_my_account(self, step):
        def is_account_page():
            return self.is_account_page(self.driver.page_source)

        if is_account_page():
            time.sleep(1)
            if not is_account_page():
                return self.action_tap_my_account(step)
            print("[TAP_MY_ACCOUNT] 已在我的账户页，跳过入口点击")
            time.sleep(step.get("wait_after", 1))
            return

        if self.is_login_required(self.driver.page_source) or self.is_login_page(self.driver.page_source):
            self.ensure_logged_in("tap_my_account")
            if is_account_page():
                time.sleep(step.get("wait_after", 1))
                return

        attempts = int(step.get("attempts", 4))
        wait_each = float(step.get("attempt_wait", 1.5))
        locator_timeout = float(step.get("locator_timeout", 3))
        for attempt in range(1, attempts + 1):
            print(f"[TAP_MY_ACCOUNT] 点击入口，第 {attempt}/{attempts} 次")
            try:
                el = self.wait_for_element(
                    {
                        "by": "android_uiautomator",
                        "value": 'new UiSelector().descriptionContains("我的账户")',
                    },
                    timeout=locator_timeout,
                )
                self.click_element_center(el)
            except TimeoutException:
                if "x" not in step or "y" not in step:
                    self.artifacts.save(self.driver, 0, "tap_my_account_entry_not_found")
                    raise AssertionError("进入我的账户失败：当前页面没有找到“我的账户”入口")
                self.driver.execute_script(
                    "mobile: clickGesture",
                    {"x": int(step["x"]), "y": int(step["y"])},
                )
            time.sleep(wait_each)
            if self.is_login_required(self.driver.page_source) or self.is_login_page(self.driver.page_source):
                self.ensure_logged_in("tap_my_account")
                continue
            if is_account_page():
                time.sleep(step.get("wait_after", 1))
                return

        self.artifacts.save(self.driver, 0, "tap_my_account_failed")
        raise AssertionError("进入我的账户失败：多次点击入口后仍未检测到用户名/生日/性别字段")

    def ensure_logged_in(self, reason=""):
        page_source = self.driver.page_source
        if self.is_logged_in(page_source):
            return True
        if not (self.is_login_required(page_source) or self.is_login_page(page_source)):
            return False
        if not AURO_AUTO_LOGIN:
            self.artifacts.save(self.driver, 0, "auto_login_disabled")
            raise AssertionError("登录前置失败：AURO_AUTO_LOGIN 已关闭，无法自动恢复未登录状态")
        if not AURO_LOGIN_ACCOUNT or not AURO_LOGIN_PASSWORD:
            self.artifacts.save(self.driver, 0, "auto_login_missing_credentials")
            raise AssertionError(
                "登录前置失败：缺少 AURO_LOGIN_ACCOUNT/AURO_LOGIN_PASSWORD，"
                "请在夜跑环境变量中配置测试账号和密码"
            )
        if self.context.get("auto_login_attempted"):
            self.artifacts.save(self.driver, 0, "auto_login_retry_blocked")
            raise AssertionError("登录前置失败：已经自动登录过一次，仍未恢复到已登录状态")

        self.context["auto_login_attempted"] = True
        self.perform_login(reason=reason)
        return True

    def perform_login(self, reason=""):
        safe_print("[AUTO_LOGIN]", f"reason={reason}")
        self.artifacts.save(self.driver, 0, "auto_login_before")

        if self.is_login_required(self.driver.page_source):
            clicked = self.click_optional([
                {"by": "accessibility_id", "value": "确定"},
                {
                    "by": "android_uiautomator",
                    "value": 'new UiSelector().descriptionContains("确定")',
                },
                {
                    "by": "android_uiautomator",
                    "value": 'new UiSelector().className("android.widget.Button").instance(1)',
                },
            ], timeout=3)
            if not clicked:
                self.driver.press_keycode(4)
            time.sleep(1)

        if self.is_login_entry_page(self.driver.page_source):
            clicked = self.click_optional([
                {"by": "accessibility_id", "value": "点击登录"},
                {
                    "by": "android_uiautomator",
                    "value": 'new UiSelector().descriptionContains("点击登录")',
                },
            ])
            if not clicked:
                raise AssertionError("登录前置失败：页面显示未登录，但找不到“点击登录”入口")
            time.sleep(2)

        account_input = self.wait_for_element(
            {
                "by": "android_uiautomator",
                "value": 'new UiSelector().className("android.widget.EditText").instance(0)',
            },
            timeout=DEFAULT_TIMEOUT,
        )
        password_input = self.wait_for_element(
            {
                "by": "android_uiautomator",
                "value": 'new UiSelector().className("android.widget.EditText").instance(1)',
            },
            timeout=DEFAULT_TIMEOUT,
        )

        account_input.click()
        try:
            account_input.clear()
        except Exception:
            pass
        account_input.send_keys(AURO_LOGIN_ACCOUNT)

        password_input.click()
        try:
            password_input.clear()
        except Exception:
            pass
        password_input.send_keys(AURO_LOGIN_PASSWORD)
        self.hide_keyboard_if_present()

        checkbox = self.optional_element({"by": "class_name", "value": "android.widget.CheckBox"}, timeout=3)
        if checkbox:
            try:
                checked = str(checkbox.get_attribute("checked")).lower() == "true"
            except Exception:
                checked = False
            if not checked:
                checkbox.click()
                time.sleep(0.5)

        clicked_login = self.click_optional([
            {"by": "accessibility_id", "value": "登录"},
            {
                "by": "android_uiautomator",
                "value": 'new UiSelector().descriptionContains("登录")',
            },
            {
                "by": "android_uiautomator",
                "value": 'new UiSelector().className("android.widget.Button").instance(0)',
            },
        ], timeout=5)
        if not clicked_login:
            raise AssertionError("登录前置失败：找不到登录按钮")

        try:
            WebDriverWait(self.driver, DEFAULT_TIMEOUT).until(
                lambda d: self.is_logged_in(d.page_source)
            )
        except TimeoutException:
            self.artifacts.save(self.driver, 0, "auto_login_failed")
            raise AssertionError("登录前置失败：已提交登录，但未检测到登录成功状态")

        self.artifacts.save(self.driver, 0, "auto_login_success")

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
        if role == "bottom_mic":
            resolved = self.resolve_bottom_mic_point()
            if resolved:
                x, y = resolved
        self.long_press_point(x, y, step)

    def resolve_bottom_mic_point(self):
        """底部麦克风按钮的位置会随 App 版本变化（手机+耳机模式页曾在
        1.0.20 更新后整体上移，固定比例坐标按空导致长按无反应），
        优先按 content-desc 动态取元素中心，找不到再回退固定比例。"""
        try:
            locator = to_appium_locator(
                {"by": "accessibility_id", "value": "手机麦克风"}
            )
            el = WebDriverWait(self.driver, MIC_POINT_RESOLVE_TIMEOUT).until(
                EC.presence_of_element_located(locator)
            )
        except TimeoutException:
            return None
        rect = el.rect
        center = (rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2)
        safe_print("[LONG_PRESS]", f"已动态定位 手机麦克风 中心: {center}")
        return center

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
        last_semantic_fail = None
        saw_dual_earbuds_disconnected = False

        while time.time() < end_time:
            page_source = self.driver.page_source
            if self.is_dual_earbuds_disconnected(page_source):
                saw_dual_earbuds_disconnected = True

            blocker = self.find_blocker(page_source)
            if blocker:
                self.artifacts.save(self.driver, 0, "blocked_assert_translation")
                raise AssertionError(
                    f"等待翻译结果时检测到阻断信息：{blocker}"
                )

            page_texts = extract_visible_texts(page_source)
            last_texts = page_texts
            source_text, target_text, snippets = resolve_translation_pair(
                page_texts,
                baseline_texts,
                source_lang,
                target_lang,
            )
            last_source = source_text
            last_target = target_text

            if source_text and target_text:
                if (
                    not getattr(self.translation, "api_key", True)
                    and text_matches_language(source_text, source_lang)
                    and text_matches_language(target_text, target_lang)
                    and source_text != target_text
                ):
                    safe_print(
                        "[TRANSLATION]",
                        f"source={source_text}",
                        f"target={target_text}",
                        "模型密钥未配置；按页面已出现源文和目标译文判定音频注入成功",
                    )
                    return
                if not getattr(self.translation, "api_key", True):
                    safe_print(
                        "[TRANSLATION]",
                        "页面候选尚未同时匹配源语言和目标语言，继续等待真实译文",
                        f"source={source_text}",
                        f"target={target_text}",
                    )
                    time.sleep(poll_interval)
                    continue
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
                    f"snippets={len(snippets)}",
                    f"back_translation={result['back_translation']}",
                    f"pass={result['pass']}",
                )
                if result["pass"]:
                    return
                if not step.get("skip_vision_fallback", False):
                    try:
                        screenshot_b64 = self.driver.get_screenshot_as_base64()
                        vres = self.translation.validate_translation_with_screenshot(
                            source_text=source_text,
                            target_text=target_text,
                            source_lang=source_lang,
                            target_lang=target_lang,
                            screenshot_base64=screenshot_b64,
                            structured_snippets=snippets,
                        )
                        safe_print(
                            "[TRANSLATION_VISION]",
                            f"pass={vres['pass']}",
                            f"screen_src={vres.get('source_read_from_screen', '')}",
                            f"screen_tgt={vres.get('target_read_from_screen', '')}",
                            f"reason={vres.get('reason', '')}",
                        )
                        if vres.get("pass"):
                            return
                    except Exception as exc:
                        safe_print("[TRANSLATION_VISION]", f"skipped: {exc}")
                last_semantic_fail = (
                    f"source={source_text} | target={target_text} | "
                    f"back_translation={result['back_translation']} | "
                    f"reason={result['reason'] or '模型判定不一致'}"
                )
                safe_print(
                    "[TRANSLATION]",
                    "语义未通过，可能为页面示例/错配文案，继续轮询等待",
                    last_semantic_fail,
                )
            time.sleep(poll_interval)

        detail = ""
        if last_semantic_fail:
            detail = f" 最后一次语义校验：{last_semantic_fail}"
        if saw_dual_earbuds_disconnected:
            detail += " 双耳机页面曾显示未连接；已继续检查页面文本，但未抓到可通过的翻译结果。"
        raise AssertionError(
            "等待翻译结果超时："
            f"source_lang={source_lang}, target_lang={target_lang}, "
            f"last_source={last_source}, last_target={last_target}, "
            f"visible_texts={last_texts}.{detail}"
        )

    def action_assert_image_translation(self, step):
        wait_before = float(step.get("wait_before", 5))
        timeout = float(step.get("timeout", 60))
        poll_interval = float(step.get("poll_interval", 6))
        if wait_before > 0:
            time.sleep(wait_before)

        deadline = time.time() + timeout
        attempt = 0
        last_result = None

        while True:
            page_source = self.driver.page_source
            blocker = self.find_blocker(page_source)
            if blocker:
                self.artifacts.save(self.driver, 0, "blocked_assert_image_translation")
                raise AssertionError(
                    f"图片翻译校验前检测到阻断信息：{blocker}"
                )

            attempt += 1
            screenshot_base64 = self.driver.get_screenshot_as_base64()
            result = self.image_translation.validate_image_translation(
                screenshot_base64=screenshot_base64,
                source_lang=step.get("source_lang"),
                target_lang=step.get("target_lang"),
            )
            last_result = result
            safe_print(
                "[IMAGE_TRANSLATION]",
                f"attempt={attempt}",
                f"source={result['source_text']}",
                f"translated={result['translated_text']}",
                f"evidence={result['evidence']}",
                f"pass={result['pass']}",
            )
            if result["pass"]:
                return

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))

        result = last_result or {
            "reason": "图片翻译校验超时，未获取到模型判定结果",
            "source_text": "",
            "translated_text": "",
            "evidence": "",
        }
        raise AssertionError(
            "图片翻译视觉校验超时或失败："
            f"source={result['source_text']} | "
            f"translated={result['translated_text']} | "
            f"evidence={result['evidence']} | "
            f"reason={result['reason'] or '模型判定图片没有完成翻译'}"
        )
