"""
按 nightly 相同格式，用当前 nightly_config.local.json 里的 report_base_url
重新推送「最新一份」recording_report_bundle 的钉钉通知（不跑 pytest）。

用法（在项目根目录）:
    python scripts/resend_dingtalk_report.py
    python scripts/resend_dingtalk_report.py --bundle reports/recording_report_bundle_20260511_233040
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def _load_nightly_module():
    project_root = Path(__file__).resolve().parents[1]
    path = project_root / "scripts" / "nightly_run.py"
    spec = importlib.util.spec_from_file_location("nightly_run", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    parser = argparse.ArgumentParser(description="Resend DingTalk for latest (or given) report bundle.")
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help="指定 bundle 目录；默认取 reports 下修改时间最新的一份",
    )
    args = parser.parse_args()

    nr = _load_nightly_module()
    config = nr.load_config()

    if args.bundle:
        bundle = args.bundle.resolve()
        if not bundle.is_dir():
            print(f"目录不存在: {bundle}", file=sys.stderr)
            return 1
    else:
        bundle = nr.latest_report_bundle()
        if not bundle:
            print("没有找到 reports/recording_report_bundle_*，无法推送。", file=sys.stderr)
            return 1

    base_url = nr.build_report_base_url(config)
    manifest = nr.read_manifest(bundle)
    link = nr.report_link(bundle, base_url)
    failed = nr.failed_cases_from_report(bundle)
    payload = nr.dingtalk_markdown(
        config,
        manifest,
        link,
        exit_code=0,
        failed_cases=failed or None,
    )
    result = nr.send_dingtalk(config, payload)
    print("钉钉推送成功:", result)
    print("报告链接:", link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
