"""OAuth helpers for remote MCP servers."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


TOKEN_DIR = Path("data/mcp_tokens")


def token_path(server_key: str) -> Path:
    return TOKEN_DIR / f"{server_key}.json"


def has_access_token(server_key: str) -> bool:
    path = token_path(server_key)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool((data.get("tokens") or {}).get("access_token"))


class JsonTokenStorage(TokenStorage):
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    async def get_tokens(self) -> OAuthToken | None:
        data = self._read().get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        token_data = tokens.model_dump(mode="json", exclude_none=True)
        data["tokens"] = token_data
        if tokens.expires_in:
            data["expires_at"] = time.time() + float(tokens.expires_in)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)
