"""Telegram Bot API adapter.

Everything that knows about HTTP, tokens or method names lives here. Command handlers receive a
plain async client and never import Telegram types, so a Bot API change touches only this file.
The base URL is injectable, which is how the tests point the bot at a local fake server.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    """Raised when the Bot API answers with ``ok: false`` or the transport fails."""


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.telegram.org",
        proxy: str = "",
        timeout: float = 40.0,
    ) -> None:
        if not token:
            raise ValueError("telegram token is empty")
        self._url = f"{base_url.rstrip('/')}/bot{token}"
        self._proxy = proxy or None
        self._timeout = timeout

    async def call(self, method: str, **params: Any) -> Any:
        # trust_env=False on purpose: routing must depend only on an explicitly configured proxy,
        # never on ambient HTTP_PROXY (which also breaks localhost in some sandboxes).
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, proxy=self._proxy, trust_env=False
            ) as client:
                response = await client.post(f"{self._url}/{method}", json=params)
        except httpx.HTTPError as exc:  # transport, DNS, proxy
            raise TelegramError(f"{method}: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not response.is_success or not payload.get("ok"):
            reason = payload.get("description") or f"HTTP {response.status_code}"
            raise TelegramError(f"{method}: {reason}")
        return payload.get("result")

    async def get_me(self) -> dict[str, Any]:
        return dict(await self.call("getMe"))

    async def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        result = await self.call("getUpdates", **params)
        return list(result or [])

    async def send_message(self, chat_id: int, text: str) -> Any:
        return await self.call(
            "sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True
        )

    async def set_my_commands(self, commands: list[dict[str, str]]) -> Any:
        return await self.call("setMyCommands", commands=commands)
