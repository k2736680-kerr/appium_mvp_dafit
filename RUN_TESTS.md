# 如何运行录制回放测试

本项目日常运行建议使用 **CMD**，不是 PowerShell。

HTML 报告参数已经写进 `pytest.ini`，所以运行 pytest 时不需要再手动带 `--html`。

## 1. 启动 Android 模拟器

如果模拟器还没打开，先执行：

```cmd
start "" "E:\android_sdk\emulator\emulator.exe" -avd Pixel_8a
```

等待模拟器完全开机后，检查设备：

```cmd
"E:\android_sdk\platform-tools\adb.exe" devices
```

必须看到类似：

```text
emulator-5554    device
```

如果没有看到 `device`，pytest 会直接失败，因为 Appium 找不到 Android 设备。

## 2. 启动 Appium Server

另开一个 CMD 窗口执行：

```cmd
appium --address 0.0.0.0 --port 4723
```

这个窗口保持打开，不要关闭。

## 3. 进入项目目录

回到项目 CMD 窗口：

```cmd
cd /d E:\AutoTestTools\Projects\appium_mvp
```

## 4. 激活虚拟环境

```cmd
.venv\Scripts\activate.bat
```

看到命令行前面出现 `(.venv)`，说明已经进入虚拟环境。

## 5. 跑全部 recordings 用例

```cmd
set RECORDING_FILE=
pytest -v .\tests\test_run_recordings.py
```

每次 pytest 运行开始时，框架会自动重启一次 Auro AI App，避免测试开始前停留在其他页面。这个重启只发生一次，不是每条用例都重启。

## 6. 跑单个录制文件

示例：

```cmd
set RECORDING_FILE=recordings\[翻译]双耳机模式_英文转中文.py
pytest -v .\tests\test_run_recordings.py
```

如果要换用例，只改 `RECORDING_FILE` 后面的文件名。

## 7. 查看 HTML 报告

固定报告入口：

```cmd
reports\recording_report.html
```

如果生成了报告包，进入最新的：

```cmd
reports\recording_report_bundle_时间戳\recording_report.html
```

报告和截图都在同一个报告包目录下。发给别人时，发整个 `recording_report_bundle_时间戳` 文件夹。

## 8. 清空单个用例设置

如果之前设置过单个文件，想恢复跑全部：

```cmd
set RECORDING_FILE=
```

然后再执行 pytest 命令。

## 9. 夜间自动运行和钉钉通知

夜间自动运行由 `scripts\nightly_run.py` 负责：

- 每晚 23:30 运行全部 `recordings` 用例
- 启动或复用本地报告 HTTP 服务
- 只在 23:30 到次日 05:00 之间发送钉钉消息
- 白天手工运行不会发群，除非显式使用 `--notify always`

钉钉 webhook 和本机环境配置在：

```cmd
scripts\nightly_config.local.json
```

这个文件已加入 `.gitignore`，不会提交到远端。

安装 Windows 定时任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_nightly_task.ps1
```

安装后会创建两个任务：

```text
AppiumMvpReportServer：登录时启动报告 HTTP 服务
AppiumMvpNightlyRun：每天 23:30 运行测试并在允许时间窗内推送钉钉
```

报告链接默认使用本机局域网 IP，例如：

```text
http://本机IP:8876/recording_report_bundle_xxx/recording_report.html
```

如果钉钉群成员不在同一网络，或手机无法访问电脑 IP，需要在 `nightly_config.local.json`
里把 `report_base_url` 改成能访问到这台电脑的固定地址。

## 10. 常见文件名标签

```text
无标签：只回放动作
[音频]：回放 + 音频注入，不做翻译校验
[翻译]：回放 + 音频注入 + 翻译语义校验
长按录音：文件名里包含“长按录音”
P0/P1/P2/P3：按优先级顺序执行，P0 最先，P3 最后，未标记的排在 P3 后面
```

示例：

```text
recordings\睡眠中心_切换.py
recordings\[音频]会议记录_中文.py
recordings\[翻译]双耳机模式_英文转中文.py
recordings\[翻译]手机耳机_长按录音_中文转英文.py
recordings\[P0][翻译]双耳机模式_英文转中文.py
recordings\p1_帮助与支持.py
```
