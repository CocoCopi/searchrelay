from __future__ import annotations

import json
from pathlib import Path

from scripts.admin import main


def test_create_key_writes_the_keys_file(tmp_path: Path, capsys) -> None:
    keys_file = tmp_path / "keys.json"

    exit_code = main(
        [
            "create-key",
            "--account",
            "demo",
            "--balance",
            "100000000",
            "--keys-file",
            str(keys_file),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "api_key=sk-" in output
    payload = json.loads(keys_file.read_text(encoding="utf-8"))
    assert payload["balances"] == {"demo": 100000000}
    assert list(payload["keys"].values()) == ["demo"]


def test_create_key_refuses_to_replace_an_existing_account(tmp_path: Path) -> None:
    keys_file = tmp_path / "keys.json"
    args = [
        "create-key",
        "--account",
        "demo",
        "--balance",
        "1",
        "--keys-file",
        str(keys_file),
    ]
    assert main(args) == 0

    try:
        main(args)
    except SystemExit as exc:
        assert "already exists" in str(exc)
    else:  # pragma: no cover - the command must fail
        raise AssertionError("the second create-key call should have failed")


def test_set_budget_updates_the_keys_file(tmp_path: Path, capsys) -> None:
    keys_file = tmp_path / "keys.json"
    main(
        [
            "create-key",
            "--account",
            "demo",
            "--balance",
            "1000",
            "--keys-file",
            str(keys_file),
        ]
    )
    capsys.readouterr()

    assert main(
        [
            "set-budget",
            "--account",
            "demo",
            "--balance",
            "4200",
            "--keys-file",
            str(keys_file),
        ]
    ) == 0

    payload = json.loads(keys_file.read_text(encoding="utf-8"))
    assert payload["balances"]["demo"] == 4200


def test_list_keys_masks_the_key(tmp_path: Path, capsys) -> None:
    keys_file = tmp_path / "keys.json"
    main(
        [
            "create-key",
            "--account",
            "demo",
            "--balance",
            "1000",
            "--keys-file",
            str(keys_file),
        ]
    )
    capsys.readouterr()

    assert main(["list-keys", "--keys-file", str(keys_file)]) == 0
    output = capsys.readouterr().out
    assert "demo" in output
    assert "sk-" not in output.replace("sk-", "", 0) or "..." in output
    assert "..." in output


def test_usage_reports_billed_requests(tmp_path: Path, monkeypatch, capsys) -> None:
    database = tmp_path / "billing.db"
    monkeypatch.setenv("SR_DATABASE_PATH", str(database))

    from app.billing import BillingStore
    from app.config import Settings

    settings = Settings()
    store = BillingStore(settings.database_path, settings.price_micros_per_request)
    store.initialize({"demo": 10_000_000})
    assert store.charge("demo", "search") is True
    assert store.charge("demo", "fetch") is True

    assert main(["usage", "--account", "demo"]) == 0
    output = capsys.readouterr().out
    assert "search" in output
    assert "fetch" in output
    assert output.count("750") == 2
