import html
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from core.config import PROJECT_ROOT
from core.recording_rules import parse_case_labels


CUSTOM_REPORT_STYLE = """
<style>
body {
  font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
  font-size: 13px;
  color: #243042;
  background: #f5f7fb;
  margin: 0;
  padding: 24px;
}
h1 {
  margin: 0 0 8px 0;
  font-size: 28px;
  color: #111827;
}
h2 {
  color: #111827;
}
p, a, span, td, th, div {
  color: inherit;
}
.summary,
#environment,
#results-table {
  background: #ffffff;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
}
.summary {
  padding: 18px 18px 12px;
  margin: 18px 0;
}
#environment {
  margin-bottom: 18px;
}
#results-table {
  border: none;
}
#results-table th {
  background: #eef2ff;
  color: #1f2937;
  border-color: #dbe3f1;
  font-size: 12px;
  text-transform: none;
}
#results-table td {
  border-color: #e5e7eb;
  color: #1f2937;
  vertical-align: top;
}
#results-table tbody.passed .col-result {
  color: #0f9d58;
  font-weight: 600;
}
#results-table tbody.failed .col-result,
#results-table tbody.error .col-result {
  color: #d93025;
  font-weight: 600;
}
.col-caseName {
  min-width: 260px;
  font-weight: 600;
}
.col-caseMode {
  min-width: 110px;
}
.col-sourcePath {
  min-width: 280px;
  color: #475569;
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
}
.report-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(180px, 1fr));
  gap: 12px;
  margin: 12px 0 18px;
}
.report-meta__card {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 12px 14px;
}
.report-meta__label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}
.report-meta__value {
  font-size: 18px;
  font-weight: 700;
  color: #111827;
}
.case-detail {
  margin-bottom: 12px;
  padding: 12px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}
.case-detail strong {
  color: #0f172a;
}
.case-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(120px, 1fr));
  gap: 10px;
  margin: 12px 0 16px;
}
.case-summary__item {
  padding: 10px 12px;
  background: #ffffff;
  border: 1px solid #dbe3f1;
  border-radius: 8px;
}
.case-summary__label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 4px;
}
.case-summary__value {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}
.step-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin: 14px 0;
}
.step-card {
  background: #ffffff;
  border: 1px solid #dbe3f1;
  border-radius: 10px;
  overflow: hidden;
}
.step-card__thumb {
  display: block;
  background: #0f172a;
  aspect-ratio: 9 / 16;
}
.step-card__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.step-card__body {
  padding: 10px 12px 12px;
}
.step-card__index {
  font-size: 11px;
  color: #64748b;
  margin-bottom: 6px;
}
.step-card__name {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
  line-height: 1.5;
  min-height: 40px;
}
.step-card__links {
  margin-top: 10px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.step-card__links a {
  font-size: 12px;
  color: #2563eb;
  text-decoration: none;
}
.step-card__links a:hover {
  text-decoration: underline;
}
.failure-summary {
  margin: 14px 0;
  padding: 12px 14px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  color: #991b1b;
}
.failure-summary strong {
  color: #7f1d1d;
}
.logwrapper {
  background-color: #f1f5f9;
}
.logwrapper .log {
  border-color: #dbe3f1;
  background: #ffffff;
}
</style>
"""


RUN_BUNDLE_ENV = "APPIUM_RUN_BUNDLE_DIR"
ARTIFACTS_ROOT_ENV = "APPIUM_ARTIFACTS_ROOT"
REPORT_RESULTS = []


def _relative_display_path(path_value):
    try:
        return str(Path(path_value).resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except Exception:
        return str(path_value).replace("\\", "/")


def _infer_recording_report_meta(recording_file):
    path = Path(recording_file)
    raw_title = path.stem
    labels = parse_case_labels(raw_title)

    mode = "普通回放"
    if labels["enable_translation"]:
        mode = "翻译校验"
    elif labels["enable_audio"]:
        mode = "音频注入"

    return {
        "display_name": labels["clean_title"] or raw_title,
        "case_mode": mode,
        "source_path": _relative_display_path(path),
        "case_type": "录制回放",
    }


def _infer_json_case_report_meta(case_file):
    path = Path(case_file)
    display_name = path.stem
    mode = "JSON用例"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        display_name = data.get("name") or data.get("title") or display_name
        meta = data.get("recording_meta", {})
        if meta.get("enable_translation"):
            mode = "翻译校验"
        elif meta.get("enable_audio"):
            mode = "音频注入"
    except Exception:
        pass

    return {
        "display_name": display_name,
        "case_mode": mode,
        "source_path": _relative_display_path(path),
        "case_type": "JSON用例",
    }


def _infer_item_report_meta(item):
    callspec = getattr(item, "callspec", None)
    if not callspec:
        return {
            "display_name": item.name,
            "case_mode": "测试",
            "source_path": item.nodeid,
            "case_type": "测试",
        }

    params = callspec.params
    if "recording_file" in params:
        return _infer_recording_report_meta(params["recording_file"])
    if "case_file" in params:
        return _infer_json_case_report_meta(params["case_file"])

    return {
        "display_name": callspec.id or item.name,
        "case_mode": "测试",
        "source_path": item.nodeid,
        "case_type": "测试",
    }


def _safe_case_id(name: str) -> str:
    result = []
    for ch in name:
        if ch.isalnum() or ch in "_-":
            result.append(ch)
        else:
            result.append("_")
    return "".join(result).strip("_") or "recording_case"


def _bundle_dir() -> Path | None:
    bundle_dir = os.environ.get(RUN_BUNDLE_ENV)
    return Path(bundle_dir) if bundle_dir else None


def _artifact_case_dir(report) -> Path | None:
    bundle_dir = _bundle_dir()
    if not bundle_dir:
        return None
    case_dir = bundle_dir / "artifacts" / _safe_case_id(getattr(report, "display_name", report.nodeid))
    return case_dir if case_dir.exists() else None


def _collect_step_artifacts(report):
    case_dir = _artifact_case_dir(report)
    if not case_dir:
        return []

    png_files = sorted(case_dir.glob("*.png"))
    steps = []
    for png_path in png_files:
        xml_path = png_path.with_suffix(".xml")
        stem = png_path.stem
        step_no = stem.split("_", 1)[0]
        step_name = stem.split("_", 1)[1] if "_" in stem else stem
        steps.append(
            {
                "index": step_no,
                "name": step_name,
                "png_rel": png_path.relative_to(case_dir.parent.parent).as_posix(),
                "xml_rel": xml_path.relative_to(case_dir.parent.parent).as_posix() if xml_path.exists() else None,
            }
        )
    return steps


def _extract_failure_summary(report):
    text = getattr(report, "longreprtext", "") or ""
    if not text:
        return ""

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("E       "):
            return line.replace("E       ", "", 1)
    return text.splitlines()[-1].strip() if text.splitlines() else ""


def _build_step_gallery_html(report):
    steps = _collect_step_artifacts(report)
    if not steps:
        return '<div class="case-detail"><strong>步骤截图：</strong>当前未找到本用例的截图产物。</div>'

    cards = []
    for step in steps:
        png_rel = html.escape(step["png_rel"])
        xml_link = ""
        if step["xml_rel"]:
            xml_rel = html.escape(step["xml_rel"])
            xml_link = f'<a href="{xml_rel}" target="_blank">查看 XML</a>'

        cards.append(
            '<div class="step-card">'
            f'<a class="step-card__thumb" href="{png_rel}" target="_blank">'
            f'<img src="{png_rel}" alt="{html.escape(step["name"])}"/>'
            '</a>'
            '<div class="step-card__body">'
            f'<div class="step-card__index">步骤 {html.escape(step["index"])}</div>'
            f'<div class="step-card__name">{html.escape(step["name"])}</div>'
            f'<div class="step-card__links"><a href="{png_rel}" target="_blank">查看截图</a>{xml_link}</div>'
            '</div>'
            '</div>'
        )

    return '<div class="step-gallery">' + "".join(cards) + "</div>"


def _collect_step_artifacts_for_case(bundle_dir: Path, display_name: str):
    case_dir = bundle_dir / "artifacts" / _safe_case_id(display_name)
    if not case_dir.exists():
        return []

    steps = []
    for png_path in sorted(case_dir.glob("*.png")):
        xml_path = png_path.with_suffix(".xml")
        stem = png_path.stem
        step_no = stem.split("_", 1)[0]
        step_name = stem.split("_", 1)[1] if "_" in stem else stem
        steps.append(
            {
                "index": step_no,
                "name": step_name,
                "png_rel": png_path.relative_to(bundle_dir).as_posix(),
                "xml_rel": xml_path.relative_to(bundle_dir).as_posix() if xml_path.exists() else "",
            }
        )
    return steps


def _status_label(outcome: str) -> str:
    mapping = {
        "passed": "PASSED",
        "failed": "FAILED",
        "skipped": "SKIPPED",
        "error": "ERROR",
    }
    return mapping.get(outcome.lower(), outcome.upper())


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时 {minutes}分钟 {sec}秒"
    if minutes:
        return f"{minutes}分钟 {sec}秒"
    return f"{sec}秒"


def _build_portable_step_html(step):
    png_rel = html.escape(step["png_rel"])
    xml_link = ""
    if step["xml_rel"]:
        xml_rel = html.escape(step["xml_rel"])
        xml_link = f'<a href="{xml_rel}" target="_blank">XML</a>'

    return (
        '<div class="step">'
        '<div class="step-info">'
        f'<div class="step-title">步骤 {html.escape(step["index"])}：{html.escape(step["name"])}</div>'
        f'<div class="step-links"><a href="{png_rel}" target="_blank">查看截图</a>{xml_link}</div>'
        '</div>'
        f'<a class="step-shot" href="{png_rel}" target="_blank"><img src="{png_rel}" alt="{html.escape(step["name"])}"></a>'
        '</div>'
    )


def _build_portable_case_html(result, bundle_dir: Path):
    status = _status_label(result["outcome"])
    steps = _collect_step_artifacts_for_case(bundle_dir, result["display_name"])
    step_html = "".join(_build_portable_step_html(step) for step in steps)
    if not step_html:
        step_html = '<div class="empty-steps">没有找到本用例的步骤截图。</div>'

    failure_html = ""
    if result["failure_summary"]:
        failure_html = (
            '<div class="failure-box">'
            f'<strong>失败原因：</strong>{html.escape(result["failure_summary"])}'
            '</div>'
        )

    show_class = " show" if result["outcome"] != "passed" else ""
    return (
        f'<div class="test-case{show_class}" data-status="{status}">'
        '<div class="test-case-header" onclick="this.parentElement.classList.toggle(\'show\')">'
        '<div>'
        f'<div class="case-title">{html.escape(result["display_name"])}</div>'
        f'<div class="case-subtitle">{html.escape(result["case_mode"])} · {html.escape(result["source_path"])}</div>'
        '</div>'
        '<div class="case-header-right">'
        f'<span class="step-count">{len(steps)} 步</span>'
        f'<span class="status-badge {status}">{status}</span>'
        '</div>'
        '</div>'
        '<div class="test-case-body">'
        '<div class="case-meta">'
        f'<div><strong>类型：</strong>{html.escape(result["case_type"])}</div>'
        f'<div><strong>耗时：</strong>{_format_duration(result["duration"])}</div>'
        f'<div><strong>来源：</strong>{html.escape(result["source_path"])}</div>'
        '</div>'
        f'{failure_html}'
        '<h3>执行步骤与截图</h3>'
        f'{step_html}'
        '</div>'
        '</div>'
    )


def _write_recording_report(bundle_dir: Path, session, exitstatus):
    total = len(REPORT_RESULTS)
    passed = len([item for item in REPORT_RESULTS if item["outcome"] == "passed"])
    failed = len([item for item in REPORT_RESULTS if item["outcome"] == "failed"])
    skipped = len([item for item in REPORT_RESULTS if item["outcome"] == "skipped"])
    errors = len([item for item in REPORT_RESULTS if item["outcome"] == "error"])
    pass_rate = f"{(passed / total * 100):.0f}%" if total else "0%"
    duration = sum(item["duration"] for item in REPORT_RESULTS)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_id = bundle_dir.name
    cases_html = "".join(_build_portable_case_html(item, bundle_dir) for item in REPORT_RESULTS)

    manifest = {
        "report_id": report_id,
        "generated_at": generated_at,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "exitstatus": exitstatus,
    }
    (bundle_dir / "archive_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>自动化测试报告 - {html.escape(report_id)}</title>
  <style>
    body {{
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      margin: 0;
      padding: 20px;
      background-color: #f5f5f5;
      color: #333;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
      background-color: #fff;
      padding: 30px;
      border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }}
    h1 {{
      color: #333;
      border-bottom: 3px solid #4CAF50;
      padding-bottom: 10px;
      margin-top: 0;
    }}
    h2 {{
      color: #555;
      margin-top: 30px;
    }}
    h3 {{
      color: #555;
      margin: 18px 0 12px;
    }}
    .info {{
      color: #666;
      font-size: 14px;
      margin: 10px 0;
      line-height: 1.8;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 20px;
      margin: 20px 0;
    }}
    .summary-card {{
      background-color: #f9f9f9;
      padding: 20px;
      border-radius: 8px;
      text-align: center;
      border: 1px solid #eee;
    }}
    .summary-card h3 {{
      margin: 0 0 10px 0;
      color: #666;
      font-size: 14px;
      font-weight: 600;
    }}
    .summary-card .value {{
      font-size: 32px;
      font-weight: bold;
      color: #333;
    }}
    .passed {{ color: #4CAF50 !important; }}
    .failed {{ color: #f44336 !important; }}
    .skipped {{ color: #ff9800 !important; }}
    .error {{ color: #9c27b0 !important; }}
    .test-results {{
      margin-top: 20px;
    }}
    .test-case {{
      border: 1px solid #ddd;
      margin-bottom: 12px;
      border-radius: 6px;
      overflow: hidden;
      background: #fff;
    }}
    .test-case-header {{
      padding: 15px;
      background-color: #f9f9f9;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }}
    .test-case-header:hover {{
      background-color: #e9e9e9;
    }}
    .case-title {{
      font-size: 16px;
      font-weight: 700;
      color: #333;
    }}
    .case-subtitle {{
      margin-top: 6px;
      font-size: 13px;
      color: #777;
    }}
    .case-header-right {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }}
    .step-count {{
      font-size: 12px;
      color: #666;
      background: #eee;
      padding: 5px 8px;
      border-radius: 4px;
    }}
    .test-case-body {{
      padding: 16px;
      display: none;
      border-top: 1px solid #ddd;
    }}
    .test-case.show .test-case-body {{
      display: block;
    }}
    .status-badge {{
      padding: 5px 10px;
      border-radius: 4px;
      color: white;
      font-weight: bold;
      font-size: 12px;
    }}
    .status-badge.PASSED {{ background-color: #4CAF50; }}
    .status-badge.FAILED {{ background-color: #f44336; }}
    .status-badge.SKIPPED {{ background-color: #ff9800; }}
    .status-badge.ERROR {{ background-color: #9c27b0; }}
    .case-meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      color: #666;
      font-size: 14px;
      margin-bottom: 12px;
    }}
    .failure-box {{
      padding: 12px;
      margin: 12px 0;
      background-color: #ffebee;
      border-left: 3px solid #f44336;
      color: #7f1d1d;
      line-height: 1.6;
    }}
    .step {{
      display: grid;
      grid-template-columns: minmax(240px, 1fr) 180px;
      gap: 14px;
      padding: 12px;
      margin: 8px 0;
      background-color: #f5f5f5;
      border-left: 3px solid #4CAF50;
      border-radius: 4px;
      align-items: start;
    }}
    .step-title {{
      font-weight: 700;
      color: #333;
      line-height: 1.6;
    }}
    .step-links {{
      margin-top: 8px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .step-links a {{
      color: #1976D2;
      text-decoration: none;
      font-size: 13px;
    }}
    .step-links a:hover {{
      text-decoration: underline;
    }}
    .step-shot {{
      display: block;
      width: 180px;
      height: 320px;
      background: #111;
      border-radius: 6px;
      overflow: hidden;
      border: 1px solid #ddd;
    }}
    .step-shot img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .empty-steps {{
      padding: 12px;
      background: #fff8e1;
      border-left: 3px solid #ff9800;
      color: #795548;
    }}
    @media (max-width: 760px) {{
      .container {{ padding: 18px; }}
      .test-case-header {{ align-items: flex-start; flex-direction: column; }}
      .step {{ grid-template-columns: 1fr; }}
      .step-shot {{ width: 100%; height: auto; aspect-ratio: 9 / 16; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>自动化测试报告</h1>
    <div class="info">
      <p><strong>报告ID:</strong> {html.escape(report_id)}</p>
      <p><strong>应用名称:</strong> Auro AI</p>
      <p><strong>生成时间:</strong> {html.escape(generated_at)}</p>
      <p><strong>执行时长:</strong> {html.escape(_format_duration(duration))}</p>
      <p><strong>项目路径:</strong> {html.escape(str(PROJECT_ROOT))}</p>
    </div>
    <h2>测试摘要</h2>
    <div class="summary">
      <div class="summary-card"><h3>总测试用例</h3><div class="value">{total}</div></div>
      <div class="summary-card"><h3>通过</h3><div class="value passed">{passed}</div></div>
      <div class="summary-card"><h3>失败</h3><div class="value failed">{failed}</div></div>
      <div class="summary-card"><h3>跳过</h3><div class="value skipped">{skipped}</div></div>
      <div class="summary-card"><h3>错误</h3><div class="value error">{errors}</div></div>
      <div class="summary-card"><h3>通过率</h3><div class="value">{pass_rate}</div></div>
    </div>
    <h2>测试详情</h2>
    <div class="test-results">
      {cases_html}
    </div>
  </div>
  <script>
    document.querySelectorAll('.test-case[data-status="FAILED"], .test-case[data-status="ERROR"]').forEach(function(el) {{
      el.classList.add('show');
    }});
  </script>
</body>
</html>
"""
    htmlpath = session.config.getoption("htmlpath")
    report_name = Path(htmlpath).name if htmlpath else "recording_report.html"
    (bundle_dir / report_name).write_text(html_text, encoding="utf-8")


def pytest_html_report_title(report):
    report.title = "Auro AI Appium 自动化测试报告"


def pytest_html_results_summary(prefix, summary, postfix, session):
    terminal = session.config.pluginmanager.getplugin("terminalreporter")
    stats_map = getattr(terminal, "stats", {}) if terminal else {}
    collected = session.testscollected
    passed = len(stats_map.get("passed", []))
    failed = len(stats_map.get("failed", []))
    skipped = len(stats_map.get("skipped", []))
    pass_rate = f"{(passed / collected * 100):.0f}%" if collected else "0%"
    prefix.append(CUSTOM_REPORT_STYLE)
    prefix.append(
        """
<div class="report-meta">
  <div class="report-meta__card">
    <div class="report-meta__label">项目</div>
    <div class="report-meta__value">Auro AI Appium</div>
  </div>
  <div class="report-meta__card">
    <div class="report-meta__label">用例总数</div>
    <div class="report-meta__value">%s</div>
  </div>
  <div class="report-meta__card">
    <div class="report-meta__label">通过 / 失败</div>
    <div class="report-meta__value">%s / %s</div>
  </div>
  <div class="report-meta__card">
    <div class="report-meta__label">通过率</div>
    <div class="report-meta__value">%s</div>
  </div>
</div>
"""
        % (collected, passed, failed, pass_rate)
    )


def pytest_html_results_table_header(cells):
    cells[:] = [
        '<th class="sortable" data-column-type="result">结果</th>',
        '<th class="sortable" data-column-type="caseName">用例名称</th>',
        '<th class="sortable" data-column-type="caseMode">模式</th>',
        '<th class="sortable" data-column-type="duration">耗时</th>',
        '<th class="sortable" data-column-type="sourcePath">来源文件</th>',
    ]


def pytest_html_results_table_row(report, cells):
    display_name = html.escape(getattr(report, "display_name", report.nodeid))
    case_mode = html.escape(getattr(report, "case_mode", "测试"))
    source_path = html.escape(getattr(report, "source_path", report.nodeid))
    duration_cell = cells[2] if len(cells) > 2 else '<td class="col-duration"></td>'

    cells[:] = [
        cells[0],
        f'<td class="col-caseName">{display_name}</td>',
        f'<td class="col-caseMode">{case_mode}</td>',
        duration_cell,
        f'<td class="col-sourcePath">{source_path}</td>',
    ]


def pytest_html_results_table_html(report, data):
    case_type = html.escape(getattr(report, "case_type", "测试"))
    case_mode = html.escape(getattr(report, "case_mode", "测试"))
    source_path = html.escape(getattr(report, "source_path", report.nodeid))
    display_name = html.escape(getattr(report, "display_name", report.nodeid))
    steps = _collect_step_artifacts(report)
    failure_summary = _extract_failure_summary(report)
    data.insert(
        0,
        (
            '<div class="case-detail">'
            f'<div><strong>用例名称：</strong>{display_name}</div>'
            f'<div><strong>类型：</strong>{case_type}</div>'
            f'<div><strong>模式：</strong>{case_mode}</div>'
            f'<div><strong>来源：</strong>{source_path}</div>'
            '<div class="case-summary">'
            f'<div class="case-summary__item"><div class="case-summary__label">结果</div><div class="case-summary__value">{html.escape(report.outcome.upper())}</div></div>'
            f'<div class="case-summary__item"><div class="case-summary__label">阶段</div><div class="case-summary__value">{html.escape(report.when)}</div></div>'
            f'<div class="case-summary__item"><div class="case-summary__label">步骤数</div><div class="case-summary__value">{len(steps)}</div></div>'
            f'<div class="case-summary__item"><div class="case-summary__label">来源文件</div><div class="case-summary__value">{source_path}</div></div>'
            '</div>'
            '</div>'
        ),
    )
    if failure_summary:
        data.insert(
            1,
            (
                '<div class="failure-summary">'
                f'<strong>失败摘要：</strong>{html.escape(failure_summary)}'
                '</div>'
            ),
        )
    data.append(_build_step_gallery_html(report))


def pytest_configure(config):
    htmlpath = config.getoption("htmlpath")
    if not htmlpath:
        return

    report_path = Path.cwd() / Path(htmlpath)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_dir = report_path.parent / f"{report_path.stem}_bundle_{timestamp}"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    os.environ[RUN_BUNDLE_ENV] = str(bundle_dir)
    os.environ[ARTIFACTS_ROOT_ENV] = str(bundle_dir / "artifacts")


def pytest_sessionfinish(session, exitstatus):
    bundle_dir_value = os.environ.get(RUN_BUNDLE_ENV)
    if not bundle_dir_value:
        return

    bundle_dir = Path(bundle_dir_value)
    _write_recording_report(bundle_dir, session, exitstatus)

    prelude_dir = PROJECT_ROOT / "artifacts" / "_recording_prelude"
    bundle_prelude_dir = bundle_dir / "artifacts" / "_recording_prelude"
    if prelude_dir.exists() and not bundle_prelude_dir.exists():
        shutil.copytree(prelude_dir, bundle_prelude_dir, dirs_exist_ok=True)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    meta = _infer_item_report_meta(item)
    report.display_name = meta["display_name"]
    report.case_mode = meta["case_mode"]
    report.source_path = meta["source_path"]
    report.case_type = meta["case_type"]

    should_record = report.when == "call" or (report.when in {"setup", "teardown"} and report.failed)
    if not should_record:
        return

    result_outcome = report.outcome
    if report.when != "call" and report.failed:
        result_outcome = "error"

    REPORT_RESULTS.append(
        {
            "display_name": meta["display_name"],
            "case_mode": meta["case_mode"],
            "source_path": meta["source_path"],
            "case_type": meta["case_type"],
            "outcome": result_outcome,
            "duration": float(getattr(report, "duration", 0) or 0),
            "failure_summary": _extract_failure_summary(report),
        }
    )
