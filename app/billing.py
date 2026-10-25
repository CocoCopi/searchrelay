from __future__ import annotations

import sqlite3
from pathlib import Path


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

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=10)
