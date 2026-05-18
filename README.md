# appium_mvp

## 统一动作后验证机制

项目现在包含独立的 `runner/` 平台层，用来把 Inspector 录制出的动作流水账升级为“动作 + 自动验证 + 证据报告”：

- `runner/actions.py`：统一封装 tap、input、swipe、drag、back、wait 等动作。
- `runner/state.py`：采集 page source、截图、可见文本、页面 hash。
- `runner/verifier.py`：统一执行 `page_signature`、`exists`、`not_exists`、`text_contains`、`selected`、`content_changed`、`scroll_reveal`、`dialog_opened`、`dialog_closed`、`toast`、`input_value`、`no_crash`。
- `runner/locator.py`：按 `resource-id > accessibility id/content-desc > text > class+text > bounds > instance` 选择定位器，并在报告里标记 `unstable_locator`。
- `runner/report.py`：输出 `data/reports/<case_id>_<timestamp>/report.json`、`report.md`、`screenshots/`、`page_sources/`。
- `runner/executor.py`：面向 JSON/DSL 用例的统一执行器。

`core.runner.CaseRunner` 已接入同一套验证和报告机制。每个步骤都会按以下流程执行：

```text
capture before state -> execute action -> wait -> capture after state -> verify -> save evidence/report
```

页面只需要维护标准化元数据，例如：

```json
{
  "pages": {
    "steps_detail": {
      "page_name": "步数详情页",
      "signature": {
        "must_have_texts": ["步数"],
        "any_have_texts": ["日", "周", "月"],
        "must_have_elements": ["返回按钮", "日历按钮"],
        "min_match": 2
      }
    }
  },
  "elements": {
    "home.step_card": {
      "name": "步数卡片",
      "role": "navigation_card",
      "target_page": "steps_detail",
      "verify_strategy": "page_signature"
    }
  }
}
```

如果 step 没有显式 `verify`，Runner 会按 action、role、target_page、effect_type 自动推断默认验证策略；可以通过 `unified_verification: false` 临时关闭新机制。

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
- `[P0]` / `[P1]` / `[P2]` / `[P3]`：控制批量执行顺序，P0 最先，未标记的排在 P3 后面

`[翻译]` 自动包含 `[音频]` 能力。

优先级标签可以和功能标签组合，例如：

```text
recordings\[P0][翻译]双耳机模式_英文转中文.py
recordings\p1_帮助与支持.py
```

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

## 夜间自动运行

可用 `scripts/nightly_run.py` 配合 Windows 任务计划程序做夜间自动回归和钉钉通知。

- 默认每天 23:30 运行全部录制用例
- 钉钉发送时间窗限制为 23:30 到次日 05:00
- 白天手工运行不会发群
- 报告链接由 `scripts/report_server.py` 常驻提供 HTTP 访问

本地 webhook、端口和模拟器配置在 `scripts/nightly_config.local.json`，该文件已忽略，不提交到远端。
