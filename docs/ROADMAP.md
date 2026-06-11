# IM8 Bot — Master Roadmap

> **Mission:** Make IM8 Bot the single bot the IM8 Health server needs — beating MEE6, Dyno, Carl-bot, Statbot, and Ticket Tool at their own game, then going where they can't: AI, analytics, and the world outside Discord.

**Last updated:** 2026-06-10

---

## Why we win

Paid bots make money by gating features. We're self-hosted, single-tenant, and brand-owned. Every one of their premium pitches is structurally free for us:

| Paid bot | Their premium gate | Our answer |
|---|---|---|
| MEE6 ($12/mo) | Leveling role rewards, custom bot identity, unlimited reaction roles | Already have custom branding; XP system is Phase 2; reaction roles already exist (generalize them) |
| Dyno Premium | Auto-purge, slowmode scheduling, advanced automod | Phase 1 automations + automod |
| Carl-bot Premium | >250 reaction roles, autofeeds, advanced triggers | Unlimited by design; autofeeds in Phase 4 |
| Statbot Premium ($6+/mo) | Data retention >30d, exports, channel stats | We already snapshot daily with **infinite retention**; add exports + retention analytics (Phase 3) |
| Ticket Tool Premium | Unlimited panels, transcripts, branding | Tickets + JSON transcripts already shipped; upgrade to HTML transcripts |
| All of them | "Remove our branding" | It's *our* bot. IM8 branding everywhere, free. |

And the things **no** paid bot does well, because they must serve 1M servers generically:
1. **Deep AI integration** (Claude-powered support, digests, mod assist) tuned to IM8 Health's voice.
2. **Real analytics** — retention curves, cohorts, churn risk — not vanity counters.
3. **Brand/commerce integration** — the bot as an extension of IM8 the company (store, socials, content).
4. **Health-community features** — challenges, streaks, wellness check-ins. No generic bot will ever build these.

---

## Phase 0 — Foundation Hardening (Week 1–2)
*Boring but mandatory. Everything later stands on this.*

- **DB migrations.** Replace the hardcoded `_init_tables()` with a versioned migration system (a `schema_version` table + ordered migration scripts). Right now any schema change risks the production DB.
- **Automated backups.** Nightly scheduled job: `VACUUM INTO` a timestamped copy of `im8bot.db`, rotate last 14, optionally push offsite. SQLite + WAL is fine for one guild, but only with backups.
- **Test suite.** `pytest` + `dpytest`/fixture-based tests for the engagement scorer, embed script serialization, and DB layer. The engagement engine (`compute_engagement`) feeds three features — it must be tested.
- **Config cleanup.** Move all hardcoded IDs (tracked channels in `active.py`, region role map in `regionrole.py`, admin role) into `guild_config` DB rows editable from the Mod Panel. This is what makes the bot portable to a second server later.
- **Error tracking.** Wire unhandled exceptions to a Discord #bot-errors channel (and optionally Sentry). Logs-on-disk only works if someone reads them.
- **Deployment.** Dockerfile + `docker-compose.yml`, run as a service (not `run.cmd` on a desktop). Add `/status` uptime + restart alerting.

**Exit criteria:** schema migrations work, nightly backups exist, bot survives host reboot unattended.

---

## Phase 1 — Close Every Gap With Paid Bots (Week 2–5)
*Finish the stubs, then take the table-stakes features paid bots charge for.*

### 1A. Finish the existing stubs (they're already in the Panel UI)
- **Content Scheduling (Create/View Schedules):** the `scheduled_tasks` table and embed/hook editors already exist — build the scheduler hub: list pending broadcasts, cancel, reschedule, recurring (cron) broadcasts. *Recurring announcements is a Carl-bot premium feature.*
- **Auto-Open / Auto-Close channels:** scheduled channel lock/unlock (e.g., open #daily-check-in at 6:00, close at 23:00). Dyno charges for this.
- **Basic Message broadcast:** trivial completion of the existing editor stack.

### 2B. Moderation & safety suite (the biggest missing pillar)
- **Automod layer on top of Discord AutoMod:** link/invite filtering with allowlists, mass-mention guard, spam heuristics (message burst detection), configurable per channel from the Panel.
- **Mod actions:** `/warn`, `/timeout`, `/ban` with reasons, a `mod_actions` DB table, per-member case history, and escalation rules (3 warns → timeout).
- **Raid protection:** join-rate spike detection (we already snapshot joins daily — add a rolling 10-minute window), auto-enable verification level, alert staff.
- **Audit log channel:** message edits/deletes, role changes, joins/leaves with account-age flag (alt detection).

### 1C. Leveling & XP (MEE6's flagship)
- Generalize `compute_engagement` into a persistent XP system: `member_xp` table, XP per message with anti-spam cooldown, voice-minutes XP, levels, **role rewards at level thresholds** (MEE6 premium), `/rank` card, and the existing leaderboards become views over it.
- Keep the existing quality filters (stopwords, min length) — that's already better than MEE6, which counts "lol" as engagement.

### 1D. Upgrade what we have
- **Tickets:** HTML transcripts (styled, searchable, served by the Phase 4 dashboard), ticket claim/assign for staff, priority tags, CSAT (1–5 rating DM on close), SLA timers.
- **Reaction roles → generalized role menus:** the region-role engine is solid; generalize it into unlimited button/select/reaction role menus configured from the Panel (Carl-bot's whole premium pitch).
- **Onboarding:** add a verification/rules-acceptance gate and a role-pick step; track funnel (joined → verified → introduced) for Phase 3 analytics.

**Exit criteria:** nothing a mod needs requires another bot.

---

## Phase 2 — The AI Layer (Week 4–8) ⭐ *The moat*
*This is what no paid bot does well. Use the Claude API (anthropic SDK). Haiku-tier for high-volume tasks, Sonnet/Opus-tier for digests and drafting.*

- **Support concierge in tickets.** When a ticket opens, Claude drafts a suggested first response from an IM8 FAQ knowledge base (markdown files / DB) — *staff-approve before send* at first, auto-answer for known-safe FAQ matches later. Slashes ticket response time to seconds.
- **`/ask` FAQ assistant.** Public slash command answering from the curated IM8 knowledge base only (with "I don't know, opening a ticket" fallback). Strict guardrail: **no health/medical advice — product & community questions only**, with a disclaimers footer. This matters for a health brand.
- **Weekly community digest.** Claude summarizes the week's top discussions, questions, and wins into a branded embed, auto-posted. Builds on the engagement engine — we already know which messages matter.
- **Mod assist.** Borderline-message triage: flagged messages get a Claude second-opinion summary in the staff channel ("likely sarcasm, not an attack — context: …"). Human decides; AI contextualizes.
- **Smart onboarding.** New members get a DM concierge: answer their first questions, point to channels, suggest intro template. Falls back to opening a ticket.
- **Embed copywriter.** Inside the existing embed editor: "✨ Draft with AI" button — staff type a rough idea, Claude writes on-brand announcement copy into the editor fields.
- **Transcript summarization.** Ticket close → 2-line summary + category tag stored with the transcript; feeds the analytics ("top 5 support topics this month").

**Architecture:** one `core/ai.py` service (model selection, rate limiting, token budget cap per day, prompt templates in files, full audit log of every AI call to DB). Every AI feature goes through it.

**Exit criteria:** tickets get instant drafted answers; weekly digest posts itself; AI spend is capped and logged.

---

## Phase 3 — Analytics That Statbot Can't Touch (Week 6–10)
*We already collect snapshots, joins, and engagement. Turn data into decisions.*

- **Retention & cohorts.** Weekly join cohorts → % still active after 1/4/12 weeks. This is *the* community health metric and no Discord bot offers it.
- **Churn-risk list.** Members whose activity dropped >80% vs their baseline → staff digest ("12 previously-active members went quiet this week") with optional re-engagement DM campaign.
- **Channel heatmaps.** Activity by channel × hour-of-day × day-of-week; tells staff when to post announcements and schedule events.
- **Onboarding funnel.** joined → verified → picked region → introduced → first message: conversion % at each step, surfaced in the Member Report.
- **Exports.** One-click XLSX/CSV export of any report (openpyxl), auto-attached to Discord or emailed (Phase 4). Statbot charges for raw exports.
- **Goal tracking.** Staff set targets ("5k members by Q3", "200 weekly actives") — Daily Growth report shows progress bars toward them.
- **Anomaly alerts.** Sudden spikes/drops in joins, leaves, or message volume ping staff automatically (extends raid detection).

**Exit criteria:** staff can answer "is the community healthier than last month, and why?" from one report.

---

## Phase 4 — Outside Discord (Week 8–14)
*The "stuff beyond usual Discord" — the bot becomes IM8's community operating system.*

### 4A. Web dashboard (FastAPI + simple frontend, Discord OAuth2 login)
- Live analytics dashboards (charts beat embeds for time-series).
- HTML ticket transcript viewer (staff-only links).
- Embed/webhook editor on a real screen (reuses `EmbedScript.to_json()` — the serialization layer already exists).
- Public-facing read-only stats page (member growth, leaderboard) — great for marketing.

### 4B. Social autofeeds (Carl-bot premium, but on-brand)
- Watch IM8's **Instagram / TikTok / YouTube / X** for new posts (RSS bridges/APIs) → auto-announce in #social with branded embeds and engagement CTAs.
- New blog/article watcher for im8health.com.

### 4C. Commerce & brand integration (no generic bot can ever do this)
- **Shopify integration** (if IM8 store is Shopify): product-drop announcements, restock alerts channel, order-status lookup *inside tickets* (customer gives order #, staff sees status), discount-code drops for level/challenge rewards.
- **Email reports:** weekly community KPI email to the marketing team (the bot reports *up*, not just *in*).
- **Google Sheets sync:** push KPI rows to a sheet the marketing team already lives in.

### 4D. Inbound/outbound API
- Signed inbound webhook endpoint: external systems (CRM, store, CI) can trigger Discord announcements through the bot's branded pipeline.
- Zapier/Make compatibility via that endpoint = integration with ~7,000 apps for free.

**Exit criteria:** marketing sees community data without opening Discord; social posts announce themselves; the store and the server talk to each other.

---

## Phase 5 — Health-Community Gamification (Week 12–18)
*The category-defining features. This is what makes IM8 Bot famous.*

- **Challenges engine.** Staff create a challenge ("30-day morning routine", "10k steps June") from the Panel → members join, daily check-in button posts at a set hour, streaks tracked, completion badges + role rewards + leaderboard. *This is the killer feature for a health brand's community.*
- **Streaks & badges.** Daily check-in streaks, milestone badges (7/30/100 days), profile card showing badges + level + streak.
- **Wellness check-in channel.** Auto-open mornings (uses Phase 1 auto-open), daily prompt ("What's one win today?"), streak credit for posting, weekly AI-summarized highlights.
- **Trivia/quiz nights.** Health & IM8 product trivia events with XP prizes; question banks staff-editable, AI-assisted question drafting.
- **Event tooling.** AMA/event scheduler: announcement → reminder cadence → live question-queue collection (members submit, staff sees ranked queue) → AI recap afterwards.
- **Reward shop.** Spend XP on: discount codes (4C), exclusive roles, badge colors, "ask the expert" priority slots.

**Exit criteria:** members have a daily reason to open the server that isn't a notification.

---

## Execution order (what to literally do next)

| # | Item | Phase | Effort | Why first |
|---|---|---|---|---|
| 1 | Migrations + backups + error channel | 0 | 2–3 days | Everything else touches the DB |
| 2 | Finish scheduling stubs (Create/View Schedules) | 1A | 2–3 days | UI already promises it; tables exist |
| 3 | Mod actions + audit log + automod | 1B | 1 week | Biggest functional gap vs Dyno |
| 4 | XP/leveling on top of `compute_engagement` | 1C | 1 week | Converts existing engine into MEE6-killer |
| 5 | `core/ai.py` + ticket draft-replies + `/ask` | 2 | 1 week | Highest visible wow-per-effort |
| 6 | Retention/cohorts + exports | 3 | 1 week | Data already collected; pure upside |
| 7 | Weekly AI digest + churn alerts | 2/3 | 3–4 days | Compounds 5+6 |
| 8 | FastAPI dashboard (transcripts + charts) | 4A | 2 weeks | Unlocks web-class UX |
| 9 | Social autofeeds + Shopify hooks | 4B/C | 1–2 weeks | Brand integration |
| 10 | Challenges engine | 5 | 2 weeks | Flagship community feature |

**New dependencies as phases land:** `anthropic` (Phase 2), `openpyxl`/`matplotlib` (3), `fastapi`+`uvicorn`+`httpx` (4), nothing exotic.

**Standing rules**
- Every feature configurable from the Mod Panel (no .env edits for staff).
- Every feature ships with: migration, panel UI, audit logging, and a line in `/status`.
- AI features: human-in-the-loop first, autonomy only after trust; hard daily token budget; never health/medical advice.
- SQLite stays until the dashboard demands concurrency — then revisit (Postgres is a migration away because of #1).
