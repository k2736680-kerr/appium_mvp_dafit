import argparse
import ast
import json
import re
from pathlib import Path


def unquote_python_string(value):
    try:
        return ast.literal_eval(value)
    except Exception:
        return value.strip('"\'')


def parse_recording(text):
    elements = {}
    steps = []

    # Supports common Inspector output:
    # el1 = driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="Auro AI")
    # el1.click()
    pattern = re.compile(
        r"(?P<var>\w+)\s*=\s*driver\.find_element\(\s*by\s*=\s*AppiumBy\.(?P<by>\w+)\s*,\s*value\s*=\s*(?P<value>(?:\"(?:\\.|[^\"])*\")|(?:'(?:\\.|[^'])*'))\s*\)",
        re.S,
    )

    for m in pattern.finditer(text):
        by = m.group("by")
        value = unquote_python_string(m.group("value"))
        elements[m.group("var")] = {"by": map_by(by), "value": value}

    click_pattern = re.compile(r"(?P<var>\w+)\.click\(\)")
    for m in click_pattern.finditer(text):
        var = m.group("var")
        if var not in elements:
            continue
        locator = elements[var]
        steps.append({
            "name": f"click_{var}",
            "action": "click",
            "locator": locator,
            "timeout": 30,
        })

    return steps


def map_by(by):
    mapping = {
        "ACCESSIBILITY_ID": "accessibility_id",
        "ANDROID_UIAUTOMATOR": "android_uiautomator",
        "ID": "id",
        "XPATH": "xpath",
    }
    return mapping.get(by, by.lower())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Raw Python file exported by Appium Inspector")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--title", default="Converted Inspector case")
    parser.add_argument("--out", default=None)
    parser.add_argument("--start-mode", default="launcher", choices=["launcher", "app"])
    args = parser.parse_args()

    input_path = Path(args.input)
    text = input_path.read_text(encoding="utf-8")
    steps = parse_recording(text)

    case = {
        "case_id": args.case_id,
        "title": args.title,
        "start": {
            "mode": args.start_mode,
            "no_reset": True,
            "terminate_app": False,
            "press_home": args.start_mode == "launcher",
        },
        "blockers": [
            "耳机未连接",
            "请连接蓝牙耳机以使用此功能",
            "无录音权限",
            "录音权限",
            "麦克风权限",
            "连接错误",
            "解析错误",
        ],
        "steps": steps,
    }

    out = Path(args.out) if args.out else Path("cases") / f"{args.case_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Converted {len(steps)} click steps")
    print(f"Output: {out}")
    print("Note: graphical buttons such as microphone may need to be changed to tap_role/tap_ratio once, then reused.")


if __name__ == "__main__":
    main()
