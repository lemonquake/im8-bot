# Link Sync — Channel → Google Sheet

Mirrors every link shared in the watched Discord channel into a Google Sheet:

| Column | Value |
| --- | --- |
| **A** | Discord handle (`message.author.name`) |
| **B** | Display / server name |
| **C** | The link |

Data is appended from row 2 down; row 1 is left for your header. The bot uses the
sheet's own "append after the last used row" so it always lands where the data
currently ends (the "we're at A22/B22 now" behaviour) — no row math needed.

- **Watched channel:** `1518592306012360714` (`LINKSYNC_CHANNEL_ID`)
- **Sheet:** <https://docs.google.com/spreadsheets/d/1o8P4DbcqUr_hZn5ojRJTZ4a1C9xBcd3Rh7ghAjg_JCc/edit>
- **Excluded roles** (their links are *not* captured): `1493905370975047790`,
  `1486421826799407115`, `1494719686913556543`, `1508764439820767263`

## Why a web app (and not just the public link)

A bot **cannot write to a Google Sheet anonymously** — even a publicly-editable
sheet needs a credential to write through the Sheets API. Rather than a Google
Cloud service account, the bot talks to a small **Apps Script Web App** bound to
the sheet that runs as its owner. Benefits: no GCP project, the sheet can stay
private, and **no extra Python packages** (the bot POSTs with `aiohttp`, which
already ships with discord.py).

## One-time setup

1. Open the sheet → **Extensions ▸ Apps Script**.
2. Delete the sample, paste [`google_apps_script/link_sync_webhook.gs`](../google_apps_script/link_sync_webhook.gs),
   and change `SECRET` to a long random string.
3. **Deploy ▸ New deployment ▸ Web app** — *Execute as:* **Me**, *Who has access:*
   **Anyone**. Deploy, authorize, and copy the **`/exec`** URL.
4. Add to the bot's `.env`:
   ```
   LINKSYNC_WEBHOOK_URL=<the /exec URL>
   LINKSYNC_WEBHOOK_SECRET=<the same SECRET>
   # LINKSYNC_SHEET_NAME=Sheet1   # only if you want a specific tab
   ```
5. Restart the bot, open **`/panel` ▸ Sweep Links ▸ Test Connection** — it should
   report 🟢 with the sheet name.

> Editing the script later? Redeploy the **same** deployment
> (*Deploy ▸ Manage deployments ▸ ✏️ ▸ Version: New version*) so the URL is stable.

## How duplicates are prevented (per-URL)

The `link_sync_log` table (migration **v6**) is the single source of truth. A URL
is recorded there only **after** it's been written to the sheet, and every write
path checks the log first — so the same link is never written twice, no matter
who shares it or how many times. URLs are compared by a **normalized key**
(lowercased host, no `www.`, trailing slash and `utm_*`/`fbclid`/… tracking
params stripped), so cosmetic variants of the same link collapse to one.

Three write paths, one gate:

1. **Live** — `on_message` posts links the instant they're sent.
2. **Sweep** — every `LINKSYNC_SWEEP_MINUTES` (default **10**) a job re-scans the
   channel for anything missed (downtime, a failed write).
3. **Manual** — the **Sweep Links** button on the Mod Panel runs the same sweep.

## The "where we left off" snapshot

Each sweep stores the id of the newest message it scanned in `analytics_meta`
(`linksync_cursor`), and only reads messages **after** it next time — so routine
sweeps are cheap. The cursor only advances on a clean run; if a write fails, the
affected messages stay in range and are retried on the next sweep. The first
sweep has no cursor and scans the whole channel to backfill its history.

On startup the bot also reads the links already in the sheet once
(`linksync_seeded`) and records them, so pre-existing rows are never duplicated.

## Mod Panel — Sweep Links

`/panel ▸ 🔗 Sweep Links` opens the Link Sync hub:

- **🧹 Sweep Now** — scan for links not yet in the sheet and append them.
- **📥 Backfill From Sheet** — record links already in the sheet (dedup seed).
- **🔌 Test Connection** — verify the bot can reach the sheet.
- **⏯️ Pause / Resume** — temporarily stop or restart capturing.

## Behaviour notes / defaults

- A message with several links → one row per link (handle/name repeated), capped
  at 10 links per message.
- Synced messages get a ✅ reaction as visual confirmation.
- Only `http(s)://` and `www.` links are captured (plain text, not embeds).
- While `LINKSYNC_WEBHOOK_URL` is blank the feature is completely inert — the
  listener and sweep no-op, so it's safe to ship before you finish setup.
- **Known limitation:** a link *edited into* an already-scanned old message
  isn't re-detected (sweeps move forward from the cursor). Links in new messages
  and edits to recent messages are fine.

## Swapping to a service account later

The sheet writer is isolated in [`core/sheet_sync.py`](../core/sheet_sync.py)
behind `configured` / `append_rows` / `list_urls` / `ping`. To move to a
`gspread` service-account backend, implement the same four methods and construct
that class in `cogs/linksync.py` instead — nothing else changes.
