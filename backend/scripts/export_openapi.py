"""Regenerate docs/openapi.json (the committed API contract): python -m scripts.export_openapi"""

import json
from pathlib import Path

from app.container import build_container
from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app

OUT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"

if __name__ == "__main__":
    from datetime import date

    app = create_app(container=build_container(Settings.for_tests(), clock=FixedClock(date(2026, 10, 5))))
    OUT.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
