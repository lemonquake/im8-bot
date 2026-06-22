"""
IM8 Bot — Google Sheet writer (Apps Script Web App backend)

The bot cannot write to a Google Sheet anonymously — even a publicly-editable
sheet requires a credential to write through the Sheets API. Instead of a Google
Cloud service account, this backend talks to a small **Apps Script Web App**
that is bound to the sheet and runs as its owner (see ``docs/LINK_SYNC.md`` for
the script + one-time deploy steps). The bot just makes authenticated HTTPS
POSTs, so there are no extra Python dependencies (``aiohttp`` ships with
discord.py).

The writer is deliberately tiny and self-contained so a different backend
(e.g. a ``gspread`` service-account client) could be swapped in later without
touching the cog — it only needs ``configured``, ``append_rows``, ``list_urls``
and ``ping``.
"""

import json
import asyncio
import logging

import aiohttp

logger = logging.getLogger("im8bot.sheetsync")


class SheetSyncError(Exception):
    """Raised when the sheet web app rejects a request or is unreachable."""


class SheetSync:
    """Thin async client for the IM8 Link Sync Apps Script Web App."""

    def __init__(self, url: str, secret: str, sheet_name: str = "") -> None:
        self.url = (url or "").strip()
        self.secret = secret or ""
        self.sheet_name = sheet_name or ""

    @property
    def configured(self) -> bool:
        """True once a web app URL has been supplied via config/.env."""
        return bool(self.url)

    async def _post(self, payload: dict, timeout: int = 20) -> dict:
        if not self.configured:
            raise SheetSyncError("LINKSYNC_WEBHOOK_URL is not configured.")

        body = {"secret": self.secret, "sheet": self.sheet_name, **payload}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.url,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        raise SheetSyncError(f"HTTP {resp.status}: {text[:200]}")
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        # Apps Script returns its login HTML when the deployment
                        # isn't shared with "Anyone" — surface a clear hint.
                        raise SheetSyncError(
                            "Unexpected (non-JSON) response — check the web app is "
                            "deployed with access set to 'Anyone'."
                        )
                    if not data.get("ok"):
                        raise SheetSyncError(str(data.get("error", "unknown error")))
                    return data
        except asyncio.TimeoutError:
            raise SheetSyncError("request to the sheet web app timed out")
        except aiohttp.ClientError as e:
            raise SheetSyncError(f"network error: {e}")

    async def append_rows(self, rows: list[list[str]]) -> dict:
        """Appends rows after the last used row of the sheet.

        ``rows`` is a list of ``[handle, display, url]`` lists. The web app uses
        ``getLastRow()+1`` so this lands exactly where the existing data ends
        (the "we're at A22/B22 now" behaviour), never overwriting the header.
        """
        if not rows:
            return {"ok": True, "appended": 0}
        return await self._post({"action": "append", "rows": rows})

    async def list_urls(self, column: int = 3) -> list[str]:
        """Returns the non-empty values already in ``column`` (1-based), from
        row 2 down. Used to seed the dedup log from links already in the sheet."""
        data = await self._post({"action": "list", "column": column})
        return [str(v) for v in data.get("values", []) if str(v).strip()]

    async def ping(self) -> dict:
        """Connectivity/auth check. Returns the resolved sheet name on success."""
        return await self._post({"action": "ping"})
