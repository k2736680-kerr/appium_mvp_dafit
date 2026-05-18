# 每日自动化运行环境备忘

后续编写自启动脚本、Windows 任务计划程序脚本时，优先参考本文件。

## 项目路径

```cmd
E:\AutoTestTools\Projects\appium_mvp
```

## Python 虚拟环境

```cmd
E:\AutoTestTools\Projects\appium_mvp\.venv
```

激活命令：

```cmd
cd /d E:\AutoTestTools\Projects\appium_mvp
call .venv\Scripts\activate.bat
```

## Android SDK

```cmd
E:\android_sdk
```

## Emulator

```cmd
E:\android_sdk\emulator\emulator.exe
```

查看 AVD：

```cmd
E:\android_sdk\emulator\emulator.exe -list-avds
```

启动模拟器模板：

```cmd
start "" "E:\android_sdk\emulator\emulator.exe" -avd Pixel_8a
```

如果实际 AVD 名称不是 `Pixel_8a`，以后用 `-list-avds` 查到的名称替换。

## ADB

```cmd
E:\android_sdk\platform-tools\adb.exe
```

检查设备：

```cmd
"E:\android_sdk\platform-tools\adb.exe" devices
```

## 当前测试命令

跑全部录制用例：

```cmd
cd /d E:\AutoTestTools\Projects\appium_mvp
call .venv\Scripts\activate.bat
set RECORDING_FILE=
pytest -v .\tests\test_run_recordings.py --html=reports\recording_report.html --self-contained-html
```

## 后续每日自动化脚本思路

```cmd
cd /d E:\AutoTestTools\Projects\appium_mvp
start "" "E:\android_sdk\emulator\emulator.exe" -avd Pixel_8a
timeout /t 45
"E:\android_sdk\platform-tools\adb.exe" devices
call .venv\Scripts\activate.bat
set RECORDING_FILE=
pytest -v .\tests\test_run_recordings.py --html=reports\recording_report.html --self-contained-html
```
