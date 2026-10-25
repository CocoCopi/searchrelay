from __future__ import annotations

import json
from pathlib import Path


def test_openapi_document_contains_required_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "openapi.json").read_text(encoding="utf-8"))

    assert spec["openapi"].startswith("3.1")
    assert "/v1/tools/search" in spec["paths"]
    assert "/v1/tools/fetch" in spec["paths"]
    assert spec["paths"]["/v1/tools/search"]["post"]["security"] == [{"HTTPBearer": []}]
    assert spec["paths"]["/v1/tools/fetch"]["post"]["security"] == [{"HTTPBearer": []}]

    error_schema = spec["paths"]["/v1/tools/search"]["post"]["responses"]["400"]["content"][
        "application/json"
    ]["schema"]
    assert error_schema["properties"]["error"]["required"] == [
        "message",
        "type",
        "param",
        "code",
    ]
