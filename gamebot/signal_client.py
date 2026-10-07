"""Thin client for bbernhard/signal-cli-rest-api running in json-rpc mode."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

import httpx
import websockets

log = logging.getLogger(__name__)


def rest_group_id(internal_id: str) -> str:
    """Incoming envelopes carry the raw group id; the REST API addresses groups as group.<base64>."""
    return "group." + base64.b64encode(internal_id.encode()).decode()


class SignalClient:
    def __init__(self, base_url: str, number: str):
        self.base_url = base_url.rstrip("/")
        self.number = number
        self.http = httpx.AsyncClient(base_url=self.base_url, timeout=30)
        self._join_attempted: set[str] = set()

    async def envelopes(self) -> AsyncIterator[dict]:
        """Yield incoming envelopes forever, reconnecting on failure."""
        ws_url = self.base_url.replace("http", "ws", 1) + f"/v1/receive/{self.number}"
        while True:
            try:
                async with websockets.connect(ws_url, ping_interval=30) as ws:
                    log.info("connected to %s", ws_url)
                    async for raw in ws:
                        envelope = json.loads(raw).get("envelope")
                        if envelope:
                            yield envelope
            except Exception as exc:  # noqa: BLE001 - keep the bot alive through any disconnect
                log.warning("receive connection dropped (%s); reconnecting in 5s", exc)
                await asyncio.sleep(5)

    async def send(self, group_id: str, text: str) -> None:
        resp = await self.http.post(
            "/v2/send", json={"number": self.number, "recipients": [group_id], "message": text}
        )
        if resp.is_error:
            log.error("send failed: %s %s", resp.status_code, resp.text)

    async def react(self, group_id: str, author: str, timestamp: int, emoji: str) -> None:
        resp = await self.http.post(
            f"/v1/reactions/{self.number}",
            json={"reaction": emoji, "recipient": group_id,
                  "target_author": author, "timestamp": timestamp},
        )
        if resp.is_error:
            log.error("react failed: %s %s", resp.status_code, resp.text)

    async def sync_groups(self) -> list[dict]:
        """List the bot's groups, joining any it has been invited to but not yet joined."""
        resp = await self.http.get(f"/v1/groups/{self.number}")
        if resp.is_error:
            log.warning("listing groups failed: %s %s", resp.status_code, resp.text)
            return []
        groups = resp.json() or []
        # Pending invites list members by account id, not phone number, so we can't reliably spot
        # ourselves there. Instead try joining any group we're not a full member of, once each.
        for group in groups:
            if self.number in (group.get("members") or []) or group["id"] in self._join_attempted:
                continue
            self._join_attempted.add(group["id"])
            join = await self.http.post(f"/v1/groups/{self.number}/{group['id']}/join")
            log.info("join group %s: %s %s", group.get("name"), join.status_code, join.text[:200])
        return groups
