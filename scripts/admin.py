"""Administrative CLI for SearchRelay.

Manage API keys, account budgets, and usage:

    searchrelay-admin create-key --account demo --balance 100000000
    searchrelay-admin set-budget --account demo --balance 50000000
    searchrelay-admin usage --account demo
    searchrelay-admin list-keys

The CLI reads the same environment variables as the service (``SR_KEYS_FILE``,
``SR_DATABASE_PATH``, ``SR_REDIS_URL``). Keys and balances are written to the
keys file in the format the service loads at startup.
"""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any

from app.billing import BillingStore, RedisBillingStore
from app.config import Settings


def _load_keys_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"keys": {}, "balances": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to read keys file {path}: {exc}")
    if not isinstance(payload, dict):
        raise SystemExit(f"Keys file {path} must contain a JSON object")
    keys = payload.get("keys", {})
    balances = payload.get("balances", {})
    if not isinstance(keys, dict) or not isinstance(balances, dict):
        raise SystemExit(f"Keys file {path} must contain 'keys' and 'balances' objects")
    payload["keys"] = {str(key): str(value) for key, value in keys.items()}
    payload["balances"] = {str(key): int(value) for key, value in balances.items()}
    return payload


def _write_keys_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:6]}...{key[-4:]}"


def command_create_key(args: argparse.Namespace) -> int:
    settings = Settings()
    path = Path(args.keys_file or settings.keys_file or "data/keys.json").expanduser()
    payload = _load_keys_file(path)
    key = f"sk-{secrets.token_hex(24)}"
    account = args.account
    if account in payload["balances"] and not args.force:
        raise SystemExit(
            f"Account {account!r} already exists in {path}. Use --force to add another key "
            "for it or pick another account id."
        )
    payload["keys"][key] = account
    payload["balances"][account] = int(args.balance)
    _write_keys_file(path, payload)
    print(f"api_key={key}")
    print(f"account={account}")
    print(f"balance_micros={int(args.balance)}")
    print(f"keys_file={path}")
    print("Restart the service (or reload its key file) to activate the key.")
    return 0


def command_set_budget(args: argparse.Namespace) -> int:
    settings = Settings()
    path = Path(args.keys_file or settings.keys_file or "data/keys.json").expanduser()
    payload = _load_keys_file(path)
    account = args.account
    if account not in payload["balances"]:
        raise SystemExit(f"Unknown account {account!r} in {path}")
    payload["balances"][account] = int(args.balance)
    _write_keys_file(path, payload)
    print(f"account={account}")
    print(f"balance_micros={int(args.balance)}")
    print(f"keys_file={path}")

    if args.redis_url:
        import asyncio

        from redis.asyncio import Redis

        async def apply() -> None:
            client = Redis.from_url(args.redis_url, decode_responses=True)
            try:
                await client.hset(
                    f"{settings.redis_prefix}:billing:balances",
                    account,
                    int(args.balance),
                )
            finally:
                await client.aclose()

        asyncio.run(apply())
        print(f"redis_balance_updated={args.redis_url}")
    return 0


def command_usage(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.redis_url:
        import asyncio

        from redis.asyncio import Redis

        async def collect() -> dict[str, int]:
            client = Redis.from_url(args.redis_url, decode_responses=True)
            try:
                store = RedisBillingStore(
                    client,
                    settings.price_micros_per_request,
                    prefix=f"{settings.redis_prefix}:billing",
                )
                return await store.usage(args.account)
            finally:
                await client.aclose()

        usage = asyncio.run(collect())
    else:
        store = BillingStore(settings.database_path, settings.price_micros_per_request)
        store.initialize({})
        usage = store.usage(args.account)

    if not usage:
        print("no billable usage recorded")
        return 0
    print(f"{'action':<12}{'requests':>12}{'micros':>16}{'usd':>12}")
    for action, micros in sorted(usage.items()):
        requests = micros // max(1, settings.price_micros_per_request)
        print(f"{action:<12}{requests:>12}{micros:>16}{micros / 1_000_000:>12.4f}")
    return 0


def command_list_keys(args: argparse.Namespace) -> int:
    settings = Settings()
    path = Path(args.keys_file or settings.keys_file or "data/keys.json").expanduser()
    payload = _load_keys_file(path)
    if not payload["keys"]:
        print(f"no keys configured in {path}")
        return 0
    print(f"{'account':<24}{'key':<28}{'balance_micros'}")
    for key, account in sorted(payload["keys"].items(), key=lambda item: item[1]):
        balance = payload["balances"].get(account, 0)
        print(f"{account:<24}{_mask(key):<28}{balance}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="searchrelay-admin", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-key", help="Create an API key for an account")
    create.add_argument("--account", required=True, help="Account identifier")
    create.add_argument(
        "--balance",
        type=int,
        default=0,
        help="Initial balance in micro-US dollars (default 0)",
    )
    create.add_argument("--keys-file", help="Path to the keys file")
    create.add_argument(
        "--force",
        action="store_true",
        help="Allow adding a key for an account that already exists",
    )
    create.set_defaults(handler=command_create_key)

    budget = subparsers.add_parser("set-budget", help="Set an account balance")
    budget.add_argument("--account", required=True, help="Account identifier")
    budget.add_argument(
        "--balance",
        type=int,
        required=True,
        help="New balance in micro-US dollars",
    )
    budget.add_argument("--keys-file", help="Path to the keys file")
    budget.add_argument("--redis-url", help="Also update the balance in Redis")
    budget.set_defaults(handler=command_set_budget)

    usage = subparsers.add_parser("usage", help="Show billable usage")
    usage.add_argument("--account", help="Limit the report to one account")
    usage.add_argument("--redis-url", help="Read usage from Redis instead of SQLite")
    usage.set_defaults(handler=command_usage)

    listing = subparsers.add_parser("list-keys", help="List configured accounts and keys")
    listing.add_argument("--keys-file", help="Path to the keys file")
    listing.set_defaults(handler=command_list_keys)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
