# Engagement Engine (live tracking)

How "Most Active", Daily Growth, and the Member Report rankings are computed
since the v3 migration (`live_engagement_tracking`).

## The problem this replaced

The original `compute_engagement` re-downloaded the full history of every
tracked channel through the REST API **on every refresh**, and counting
"reactions given" cost one rate-limited API call **per reaction per message**
(plus a fixed 0.25 s sleep each). A single leaderboard refresh took ~2 hours,
and it ran every 3 hours — plus again for the daily growth report and the
weekly member report.

## The model now

```
gateway events ──► engagement_daily (SQLite) ──► instant SQL rankings
                   (guild, channel, user, day) → messages, reactions
```

- **Messages** — `on_message` records meaningful messages (stopword/length
  filter) per `(tracked channel, user, UTC day)`. Thread activity rolls up to
  the tracked parent channel. Deletions of cached messages decrement.
- **Reactions given** — `on_raw_reaction_add` / `_remove` increment/decrement
  the reactor's count for today (removals floor at 0 and never create rows).
- **Staff exclusions** are applied at *read* time, so editing
  `EXCLUDED_ROLE_IDS` applies retroactively.
- Points are computed at query time (`messages × 5 + reactions × 2`), so the
  weights can change without rewriting history.

Every leaderboard, preview, member lookup, and report ranking is now a
sub-second `GROUP BY` over this table. The public boards auto-refresh hourly.

## The two remaining REST scans

| Scan | When | Cost | What it covers |
|---|---|---|---|
| **Deep history backfill** | Automatically on the first boot with no backfill on record (also re-runnable via the hub's *Backfill History* button) | The old slow scan (minutes–hours), run a single time | Last 90 days of messages **and** reactions (attributed to the message's day), archived threads included |
| **Startup catch-up** | Automatically, ~15 s after every boot | Seconds (messages only, no reaction enumeration) | The downtime gap since the last *completed* catch-up — tracked by the `engagement_catchup_through` meta marker, **not** `MAX(day)` in the table (live listeners write today's rows before the catch-up runs, which would shrink the window to one day). Capped at 14 days; also seeds the first 14 days on a fresh install |

Both merge with `MAX(existing, scanned)` per day-cell, so they are idempotent
and never double-count on top of live tracking.

Both scans are also **floored at `engagement_period_start`** (when set): the
scan cutoff is clamped to that instant and any message dated before the period
start day is dropped. This is what makes an *engagement reset* durable — after
a reset wipes `engagement_daily`, neither the catch-up nor a manual backfill
can resurrect pre-reset history on the next boot.

### Known approximations (accepted)

- Reactions added/removed **while the bot is offline** are not recoverable
  per-day and are skipped by the catch-up scan; the one-time backfill picks up
  the surviving state of older messages.
- A reaction removed on a later day than it was added decrements nothing
  (the original day keeps its point — by design, floor-at-0 semantics).
- Backfill attributes a reaction to the **message's** day, live tracking to
  the **event's** day.

## Tables / metadata

- `engagement_daily` — the aggregate (migration v3).
- `active_leaderboards` — deployed public boards, one per
  `(guild, timeframe)`; weekly and monthly boards can run side by side.
  (Replaces the legacy single-board `active_leaderboard` table; existing
  config was migrated.)
- `analytics_meta` keys: `engagement_live_since` (when live tracking began),
  `engagement_backfill` (JSON state of the deep scan: running/done/failed +
  stats), `engagement_catchup_through` (date through which the startup
  catch-up has covered messages).

## Mod Panel features (Most Active hub)

- **Detect** Today / 7 Days / 30 Days / All-Time — instant ephemeral preview.
- **Post weekly / monthly** public boards (auto-refresh hourly + on restart).
- **Member Stats** — pick any member: points/rank per timeframe + a 14-day
  sparkline.
- **Channel Insights** — per-channel share of activity + busiest day.
- **Backfill History** — the one-time deep seed described above.

## Future

`engagement_daily` is the natural substrate for the Phase-1 XP/leveling
system in [ROADMAP.md](ROADMAP.md) — XP becomes another read over the same
aggregates.
