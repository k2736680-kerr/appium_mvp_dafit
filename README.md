# appium_mvp

轻量级 Appium 自动化录制回放项目，当前主流程是：

1. 用 Appium Inspector 录制 Android 操作。
2. 把导出的 Python 文件放进 `recordings/`。
3. 运行 `tests/test_run_recordings.py`。
4. 框架自动解析 `find_element + click`，执行步骤，保存截图和 XML，生成 pytest-html 报告。

## 目录

```text
appium_mvp/
├─ core/
│  ├─ config.py
│  ├─ driver_factory.py
│  ├─ runner.py
│  ├─ artifacts.py
│  ├─ audio.py
│  ├─ locators.py
│  ├─ recording_rules.py
│  └─ translation.py
├─ recordings/
├─ tests/
│  ├─ test_run_recordings.py
│  ├─ test_recording_rules.py
│  └─ test_translation_helpers.py
├─ cases/
├─ artifacts/
├─ reports/
└─ assets/audio/
```

## 运行

启动 Appium Server：

```cmd
appium --address 0.0.0.0 --port 4723
```

运行全部录制文件：

```powershell
Remove-Item Env:RECORDING_FILE -ErrorAction SilentlyContinue
pytest -v .\tests\test_run_recordings.py --html=reports\recording_report.html --self-contained-html
```

运行单条录制文件：

```cmd
set RECORDING_FILE=recordings\双耳机模式_英文转中文.py
pytest -v tests\test_run_recordings.py --html=reports\recording_report.html --self-contained-html
```

## 录制文件标签

默认情况下，录制文件只做动作回放。

- 无标签：只回放操作，例如 `拍照流程.py`
- `[音频]`：回放 + 自动播放音频，例如 `[音频]会议记录_中文.py`
- `[翻译]`：回放 + 自动播放音频 + 翻译语义校验，例如 `[翻译]双耳机模式_中文转英文.py`

`[翻译]` 自动包含 `[音频]` 能力。

## 音频规则

- 音频文件放在 `assets/audio/`
- 命名规则：`source_zh.wav`、`source_en.wav`、`source_ja.wav`、`source_ko.wav`
- 根据用例名自动选择源语言音频
- 目标音频不存在时自动回退 `source_en.wav`

启用 `[音频]` 或 `[翻译]` 的录制文件需要保留两个麦克风点击：

1. 第一次点击：开始录音
2. 中间自动插入 `play_audio`
3. 第二次点击：停止录音

## 翻译校验

`[翻译]` 用例会在录音结束后：

1. 从页面 XML 提取源文本和目标译文
2. 调用阿里云兼容 OpenAI 的接口做反向翻译
3. 判断反向翻译与源文本是否语义一致

支持的环境变量：

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

## 当前规则

- 右上角 `未连接` 不判失败
- `耳机未连接`、`请连接蓝牙耳机以使用此功能`、`录音权限`、`连接错误`、`解析错误` 等阻断弹窗判失败
- 现有固定坐标规则仍由框架统一接管，不要求手改录制文件
