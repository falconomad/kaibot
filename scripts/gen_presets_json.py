#!/usr/bin/env python3
"""Regenerate apps/web/src/data/presets.json from config/presets.yaml (M5 S51). tests/test_presets.py checks sync."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def build() -> str:
    spec = yaml.safe_load((ROOT / "config" / "presets.yaml").read_text())
    return json.dumps(spec, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    (ROOT / "apps" / "web" / "src" / "data" / "presets.json").write_text(build())
    print("wrote apps/web/src/data/presets.json")
