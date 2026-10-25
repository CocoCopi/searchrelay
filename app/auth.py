from __future__ import annotations

import hmac

from app.errors import APIError


class AccountResolver:
    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = dict(keys)

    def resolve(self, authorization: str | None) -> str:
        if not authorization:
            raise APIError(401, "Missing bearer API key", "invalid_api_key")

        parts = authorization.split()
        if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1]:
            raise APIError(401, "Invalid bearer API key", "invalid_api_key")

        supplied = parts[1]
        account_id: str | None = None
        for configured, candidate in self._keys.items():
            if hmac.compare_digest(supplied, configured):
                account_id = candidate
                break
        if account_id is None:
            raise APIError(401, "Invalid bearer API key", "invalid_api_key")
        return account_id
