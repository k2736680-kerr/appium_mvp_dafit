import json
import os
from pathlib import Path

from core.config import PROJECT_ROOT


class ArtifactManager:
    def __init__(self, case_id):
        self.case_id = case_id
        artifacts_root = os.environ.get("APPIUM_ARTIFACTS_ROOT")
        if artifacts_root:
            base_dir = Path(artifacts_root)
        else:
            base_dir = PROJECT_ROOT / "artifacts"
        self.case_dir = base_dir / case_id
        self.case_dir.mkdir(parents=True, exist_ok=True)

    def _safe_name(self, name):
        keep = []
        for ch in str(name):
            if ch.isalnum() or ch in "._-":
                keep.append(ch)
            else:
                keep.append("_")
        return "".join(keep)[:120]

    def save(self, driver, step_index, name):
        safe = self._safe_name(f"{step_index:03d}_{name}")
        png_path = self.case_dir / f"{safe}.png"
        xml_path = self.case_dir / f"{safe}.xml"

        try:
            driver.save_screenshot(str(png_path))
        except Exception:
            pass

        try:
            xml_path.write_text(driver.page_source, encoding="utf-8")
        except Exception:
            pass

    def save_json(self, name, data):
        path = self.case_dir / f"{self._safe_name(name)}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
