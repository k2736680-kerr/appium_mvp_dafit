el25 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="我的")
el25.click()
el26 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="我的账户")
el26.click()
el27 = driver.find_element(by=AppiumBy.ANDROID_UIAUTOMATOR, value="new UiSelector().description(\"用户名\n789\")")
el27.click()
el28 = driver.find_element(by=AppiumBy.CLASS_NAME, value="android.widget.EditText")
el28.click()
el28.clear()
el29 = driver.find_element(by=AppiumBy.CLASS_NAME, value="android.widget.EditText")
el29.click()
el29.send_keys("986")
el30 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="确定")
el30.click()
el31 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="提交")
el31.click()



