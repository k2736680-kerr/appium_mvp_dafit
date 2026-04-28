import json
import os
from pathlib import Path

import pytest

from core.driver_factory import create_driver
from core.runner import CaseRunner
from core.config import PROJECT_ROOT


def load_case_files():
    case_file = os.environ.get("CASE_FILE")
    if case_file:
        return [Path(case_file)]
    return sorted((PROJECT_ROOT / "cases").glob("*.json"))


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


CASE_FILES = load_case_files()


@pytest.mark.parametrize("case_file", CASE_FILES, ids=[p.stem for p in CASE_FILES])
def test_run_case(case_file):
    case_data = load_json(case_file)
    if case_data.get("skip_reason"):
        pytest.skip(case_data["skip_reason"])
    start = case_data.get("start", {})
    start_mode = start.get("mode", "app")
    no_reset = start.get("no_reset", True)

    driver = create_driver(start_mode=start_mode, no_reset=no_reset)
    try:
        runner = CaseRunner(driver, case_data)
        runner.run()
    finally:
        driver.quit()
