# appium_mvp

轻量级 Appium 录制回放项目。当前主链路只有一条：

1. 用 Appium Inspector 录制 Android 操作。
2. 把导出的 Python 文件放进 `recordings/`。
3. 由 `tests/test_run_recordings.py` 解析并回放。
4. 框架统一处理启动 App、公共控件适配、音频注入、自动登录、截图/XML、报告归档。

## 目录职责

```text
appium_mvp/
├─ core/                 # 平台能力：配置、驱动、runner、音频、断言、翻译校验
├─ recordings/           # Appium Inspector 导出的真实录制用例
├─ tests/                # pytest 入口和平台能力单测
├─ scripts/              # 夜跑、报告服务、定时任务安装脚本
├─ assets/audio/         # 录音/翻译用例注入的 wav 音频
└─ reports/              # 运行报告和夜跑日志，本地生成，不提交
```

临时目录：`.generated/`、`.tmp/`、`.pytest_cache/`、`__pycache__/` 都是运行产物，可以随时删除。

## 本地配置

主要配置在 `core/config.py`，默认值都可以通过环境变量覆盖。

常用环境变量：

```text
APPIUM_SERVER=http://127.0.0.1:4723
ANDROID_UDID=emulator-5554
ANDROID_ADB=D:\AutoTestTools\Tools\AndroidSdk\platform-tools\adb.exe
ANDROID_EMULATOR=D:\AutoTestTools\Tools\AndroidSdk\emulator\emulator.exe
ANDROID_AVD=Pixel_8a
ANDROID_ALLOW_HOST_AUDIO=1
ANDROID_AUDIO_BACKEND=dsound
AURO_AUTO_LOGIN=1
AURO_LOGIN_ACCOUNT=<测试账号>
AURO_LOGIN_PASSWORD=<测试密码>
```

夜跑的本机配置放在 `scripts/nightly_config.local.json`，它已经在 `.gitignore` 里，不提交远端。这里保存钉钉 webhook、七牛上传密钥、本机报告地址、模拟器启动参数和自动登录账号。

## 手工运行

建议在 CMD 里运行：

```cmd
cd /d E:\AutoTestTools\Projects\appium_mvp
call .venv\Scripts\activate.bat
```

启动 Appium Server：

```cmd
appium --address 0.0.0.0 --port 4723
```

检查设备：

```cmd
"E:\android_sdk\platform-tools\adb.exe" devices
```

运行全部录制用例：

```cmd
set RECORDING_FILE=
pytest -v .\tests\test_run_recordings.py
```

运行单条录制用例：

```cmd
set RECORDING_FILE=recordings\[P0][翻译]单向模式_英文转中文.py
pytest -v .\tests\test_run_recordings.py
```

HTML 报告参数已经写在 `pytest.ini`，不用再手动追加 `--html`。

## 录制文件规则

只有 `recordings/*.py` 会被批量执行。根目录下散落的录制脚本不会进入主链路。

文件名标签：

```text
无标签：只回放动作
[音频]：回放 + 音频注入
[翻译]：回放 + 音频注入 + 翻译语义校验
[P0]/[P1]/[P2]/[P3]：控制批量执行顺序，P0 最先
```

`[翻译]` 自动包含 `[音频]` 能力。启用音频能力的用例需要保留开始录音和停止录音两个麦克风点击，runner 会在中间插入播放音频步骤。

## 音频方案

当前使用原来的主机虚拟声卡方案，不走 gRPC。

- 音频文件放在 `assets/audio/`
- 默认文件：`source_zh.wav`、`source_en.wav`、`source_ja.wav`、`source_ko.wav`
- runner 根据用例名选择音频，找不到时回退到 `source_en.wav`
- 模拟器启动时使用 `-allow-host-audio -audio dsound`
- Windows 播放设备需要路由到 VB-CABLE，模拟器麦克风从主机音频获取输入

## 自动登录

runner 支持按需自动登录。遇到“请登录/需要登录”弹窗或登录页时，会使用测试账号恢复登录，然后回到原动作继续执行。

- `AURO_AUTO_LOGIN=0/false/no` 可关闭
- 缺少 `AURO_LOGIN_ACCOUNT` 或 `AURO_LOGIN_PASSWORD` 时会明确报“登录前置失败”
- 自动登录只做未登录恢复，不主动登出，不复用整条“登出和登录”录制用例
- `recordings/[P0]登出和登录.py` 仍可作为普通录制用例存在

## 翻译校验

`[翻译]` 用例在录音结束后会从页面 XML 提取源文本和目标译文，并调用模型做反向翻译语义判断。

当前 Auro 1.2.25 已移除双耳机入口，因此旧双耳机录制文件以 `_` 开头，不进入夜跑。手机模式使用点击开始/再次点击停止；单向模式会自动关闭“请保持屏幕开启”提示，并在播放音频前等待右上角状态变为“已连接”。手机模式则等待按钮变为“点击停止说话”。

模型相关环境变量：

```text
DASHSCOPE_API_KEY
ALI_BAICHUAN_API_KEY
ALIYUN_MODEL
ALIYUN_BASE_URL
```

默认 `ALIYUN_BASE_URL`：

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

## 夜间自动运行

夜跑由 `scripts/nightly_run.py` 负责，Windows 任务计划程序只需要安装一次。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_nightly_task.ps1
```

会创建两个任务：

```text
AppiumMvpReportServer：登录 Windows 时启动报告 HTTP 服务
AppiumMvpNightlyRun：每天 23:30 运行全部 recordings 用例并按时间窗推送钉钉
```

夜跑行为：

- 默认每天 23:30 执行
- 只在 23:30 到次日 05:00 自动推送钉钉
- 白天手工运行默认不发群，除非显式使用 `--notify always`
- 如果配置了七牛上传，钉钉报告链接会使用七牛域名
- 如果未配置七牛上传，报告链接由 `scripts/report_server.py` 常驻提供
- 跑完后保留报告服务，清理 Appium 和模拟器

七牛报告上传配置示例：

```json
{
  "qiniu_access_key": "<七牛 AccessKey>",
  "qiniu_secret_key": "<七牛 SecretKey>",
  "qiniu_bucket": "<七牛存储空间名称>",
  "qiniu_domain": "https://qcdn.moyoung.com",
  "qiniu_region": "华东-浙江",
  "qiniu_prefix": "auroai/appium-reports",
  "qiniu_upload_scope": "failed_only",
  "qiniu_upload_xml": false
}
```

`qiniu_bucket` 是七牛控制台里的存储空间名称，不是目录路径。要上传到根目录下的 `auroai/` 目录，应把 `qiniu_prefix` 配成 `auroai/appium-reports`。

## 当前公共规则

- 右上角 `未连接` 不判失败
- `耳机未连接`、`请连接蓝牙耳机以使用此功能`、`录音权限`、`连接错误`、`解析错误` 等阻断弹窗判失败
- 固定坐标和动态控件适配集中在解析/runner 层处理，不靠逐个录制文件打补丁
