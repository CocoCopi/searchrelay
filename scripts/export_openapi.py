from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import app


def main() -> int:
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    if destination is None:
        sys.stdout.write(payload)
    else:
        destination.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
