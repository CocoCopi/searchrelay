from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any, Protocol


class BillingStore:
    """Small SQLite ledger used for account balances.

    Amounts are integer micro-US dollars: one US dollar is 1,000,000 units.
    """

    def __init__(self, database_path: Path, price_micros: int) -> None:
        self.database_path = database_path.expanduser()
        self.price_micros = price_micros

    def initialize(self, initial_balances: dict[str, int]) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS balances (
                    account_id TEXT PRIMARY KEY,
                    balance_micros INTEGER NOT NULL CHECK (balance_micros >= 0)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS billing_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    amount_micros INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for account_id, balance in initial_balances.items():
                connection.execute(
                    "INSERT OR IGNORE INTO balances(account_id, balance_micros) VALUES (?, ?)",
                    (account_id, max(0, int(balance))),
                )
            connection.commit()

    def get_balance(self, account_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT balance_micros FROM balances WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def charge(self, account_id: str, action: str) -> bool:
        if self.price_micros == 0:
            return True
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT balance_micros FROM balances WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None or int(row[0]) < self.price_micros:
                connection.rollback()
                return False
            connection.execute(
                "UPDATE balances SET balance_micros = balance_micros - ? WHERE account_id = ?",
                (self.price_micros, account_id),
            )
            connection.execute(
                "INSERT INTO billing_events(account_id, action, amount_micros) VALUES (?, ?, ?)",
                (account_id, action, self.price_micros),
            )
            connection.commit()
            return True

    def usage(self, account_id: str | None = None) -> dict[str, int]:
        """Return the charged amount per action, optionally for one account."""

        with self._connect() as connection:
            if account_id:
                rows = connection.execute(
                    """
                    SELECT action, SUM(amount_micros) FROM billing_events
                    WHERE account_id = ? GROUP BY action
                    """,
                    (account_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT action, SUM(amount_micros) FROM billing_events GROUP BY action"
                ).fetchall()
        return {str(action): int(total or 0) for action, total in rows}

    def accounts(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT account_id, balance_micros FROM balances ORDER BY account_id"
            ).fetchall()
        return [(str(account), int(balance)) for account, balance in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=10)


# Atomic charge: read the balance, check it, decrement it, and record usage in
# one script so concurrent replicas cannot oversell an account.
_CHARGE_SCRIPT = """
local balances = KEYS[1]
local usage = KEYS[2]
local events = KEYS[3]
local account = ARGV[1]
local price = tonumber(ARGV[2])
local action = ARGV[3]
local entry = ARGV[4]
local balance = redis.call('HGET', balances, account)
if not balance then
    return -1
end
balance = tonumber(balance)
if balance < price then
    return 0
end
redis.call('HINCRBY', balances, account, -price)
redis.call('HINCRBY', usage, action, price)
redis.call('LPUSH', events, entry)
redis.call('LTRIM', events, 0, 9999)
return 1
"""


class RedisBillingStore:
    """Redis-backed balances and usage counters shared by every replica."""

    def __init__(
        self,
        redis: Any,
        price_micros: int,
        *,
        prefix: str = "searchrelay:billing",
    ) -> None:
        self.redis = redis
        self.price_micros = price_micros
        self.prefix = prefix

    @property
    def balances_key(self) -> str:
        return f"{self.prefix}:balances"

    @property
    def usage_key(self) -> str:
        return f"{self.prefix}:usage"

    @property
    def events_key(self) -> str:
        return f"{self.prefix}:events"

    async def initialize(self, initial_balances: dict[str, int]) -> None:
        for account_id, balance in initial_balances.items():
            await self.redis.hsetnx(self.balances_key, account_id, max(0, int(balance)))

    async def get_balance(self, account_id: str) -> int:
        value = await self.redis.hget(self.balances_key, account_id)
        if value is None:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    async def charge(self, account_id: str, action: str) -> bool:
        if self.price_micros == 0:
            return True
        entry = f"{account_id}|{action}|{self.price_micros}|{time.time():.3f}"
        result = await self.redis.eval(
            _CHARGE_SCRIPT,
            3,
            self.balances_key,
            self.usage_key,
            self.events_key,
            account_id,
            self.price_micros,
            action,
            entry,
        )
        return int(result) == 1

    async def usage(self, account_id: str | None = None) -> dict[str, int]:
        if account_id:
            entries = await self.redis.lrange(self.events_key, 0, -1)
            totals: dict[str, int] = {}
            for entry in entries:
                parts = entry.split("|")
                if len(parts) < 3 or parts[0] != account_id:
                    continue
                action = parts[1]
                try:
                    amount = int(parts[2])
                except ValueError:
                    continue
                totals[action] = totals.get(action, 0) + amount
            return totals
        raw = await self.redis.hgetall(self.usage_key)
        return {str(action): int(amount) for action, amount in raw.items()}

    async def accounts(self) -> list[tuple[str, int]]:
        raw = await self.redis.hgetall(self.balances_key)
        return sorted((str(account), int(balance)) for account, balance in raw.items())


class BillingBackend(Protocol):
    def initialize(self, initial_balances: dict[str, int]) -> Any: ...

    def get_balance(self, account_id: str) -> Any: ...

    def charge(self, account_id: str, action: str) -> Any: ...


class BillingService:
    """Async facade over the SQLite or Redis billing backend."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    @property
    def is_shared(self) -> bool:
        return isinstance(self.backend, RedisBillingStore)

    async def initialize(self, initial_balances: dict[str, int]) -> None:
        if isinstance(self.backend, RedisBillingStore):
            await self.backend.initialize(initial_balances)
            return
        await asyncio.to_thread(self.backend.initialize, initial_balances)

    async def get_balance(self, account_id: str) -> int:
        if isinstance(self.backend, RedisBillingStore):
            return await self.backend.get_balance(account_id)
        return await asyncio.to_thread(self.backend.get_balance, account_id)

    async def charge(self, account_id: str, action: str) -> bool:
        if isinstance(self.backend, RedisBillingStore):
            return await self.backend.charge(account_id, action)
        return await asyncio.to_thread(self.backend.charge, account_id, action)

    async def usage(self, account_id: str | None = None) -> dict[str, int]:
        if isinstance(self.backend, RedisBillingStore):
            return await self.backend.usage(account_id)
        return await asyncio.to_thread(self.backend.usage, account_id)

    async def accounts(self) -> list[tuple[str, int]]:
        if isinstance(self.backend, RedisBillingStore):
            return await self.backend.accounts()
        return await asyncio.to_thread(self.backend.accounts)
