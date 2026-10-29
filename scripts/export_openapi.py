#!/usr/bin/env python3
"""Export FastAPI OpenAPI schema to openapi.json in repository root."""

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.main import app

OUTPUT_FILE = REPO_ROOT / "openapi.json"


def export_openapi() -> None:
    """Generate and write OpenAPI JSON schema."""
    openapi_schema = app.openapi()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Successfully exported OpenAPI schema to {OUTPUT_FILE}")


if __name__ == "__main__":
    export_openapi()
