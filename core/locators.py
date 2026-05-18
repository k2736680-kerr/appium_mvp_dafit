from appium.webdriver.common.appiumby import AppiumBy


def to_appium_locator(locator):
    by = locator.get("by")
    value = locator.get("value")

    if by == "accessibility_id":
        return AppiumBy.ACCESSIBILITY_ID, value
    if by == "android_uiautomator":
        return AppiumBy.ANDROID_UIAUTOMATOR, value
    if by == "id":
        return AppiumBy.ID, value
    if by == "xpath":
        return AppiumBy.XPATH, value
    if by == "class_name":
        return AppiumBy.CLASS_NAME, value

    raise ValueError(f"不支持的 locator 类型: {by}")
