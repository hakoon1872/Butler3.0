# 🎩 JARVIS v4 — Night Desk

**A voice-powered AI academic butler.** FastAPI body · SQLite memory · Gemini brain · a brass-and-ink browser HUD — built for one person, properly: truth before voice, every write reversible, every memory dated.

> *"The LLM proposes, code disposes."* — the rule the whole project obeys.

| | |
|---|---|
| **Owner** | Mehtab Haider — Lahore (PKT, UTC+5) · non-programmer · free-tier builder |
| **Project status** | M0–M3 built · typed chat ✅ on his real key · local butler voice ✅ (slow) · mic input ❌ (one missing package) · Gemini Live door root-caused, fix proven, merge pending |
| **Verified checks** | 197 distinct sandbox checks green (43 + 33 + 61 + 37 + 23) |
| **Current milestone** | Phase 0 voice consolidation → then cloud (M3.5) → M4 planner → M5 learning → M6 evals → M7 Android |
| **This README** | Consolidated 19 Nov 2026 from the audit, the saved context, the M2 readme and three full chat exports — including the 18 Nov session that was never logged |
| **Companion file** | `PROJECT_PLAN.md` — the forward plan, phase by phase, with exit tests |

---

## Table of contents

1. [What Jarvis is](#1-what-jarvis-is)
2. [The story so far — everything done, in order](#2-the-story-so-far--everything-done-in-order)
3. [What works today (and what honestly doesn't)](#3-what-works-today-and-what-honestly-doesnt)
4. [Architecture in plain English](#4-architecture-in-plain-english)
5. [The three voice modes](#5-the-three-voice-modes)
6. [Feature tour](#6-feature-tour)
7. [The file map](#7-the-file-map)
8. [The data model & memory engine](#8-the-data-model--memory-engine)
9. [The grade engine & marks policy](#9-the-grade-engine--marks-policy)
10. [The tool registry & safety system](#10-the-tool-registry--safety-system)
11. [The Live-404 saga: diagnosis, proof, cure](#11-the-live-404-saga-diagnosis-proof-cure)
12. [The Kokoro voice build & its landmines](#12-the-kokoro-voice-build--its-landmines)
13. [Installation & running (laptop)](#13-installation--running-laptop)
14. [Configuration reference](#14-configuration-reference)
15. [Troubleshooting — every error seen so far](#15-troubleshooting--every-error-seen-so-far)
16. [Testing & verification](#16-testing--verification)
17. [Security & privacy](#17-security--privacy)
18. [Cloud deployment (planned next)](#18-cloud-deployment-planned-next)
19. [Roadmap](#19-roadmap)
20. [Project protocols — how instances hand off](#20-project-protocols--how-instances-hand-off)
21. [Workspace contents](#21-workspace-contents)
22. [FAQ for the owner](#22-faq-for-the-owner)
23. [Glossary](#23-glossary)

---

## 1. What Jarvis is

Jarvis is a **personal AI butler that starts with academics**: it knows your courses, your marks, your weekly time blocks and your promises; it hears you, answers with *computed* truth (never improvised maths), acts through audited tools, remembers across weeks, and — soon — nudges you unprompted and lives in the cloud so your laptop can sleep.

It began as "Butler 3.0", a working academic scheduler with a voice front end. A full audit on **15 Nov 2026** found real bugs (two silently losing data), no durable memory, no proactivity, and domain knowledge hardcoded in code. The verdict: *a working scheduler, not yet a life butler.* **Jarvis v4** is the rebuild against that audit — same soul, proper bones.

**The four design rules (audit §4):**
1. **The LLM proposes, code disposes.** Grade maths, dates, scheduling run in tested Python; the model decides intent and speaks.
2. **Data, not code.** Targets, grading scales, rules, model IDs, voices — all database rows, editable by talking or by dashboard, never by code edit.
3. **Every memory has provenance and freshness.** Who said it, when it was last confirmed, when to ask again. Vault-tier facts never enter the prompt uninvited.
4. **You approve what changes Jarvis.** Learned parameters arrive as proposals with evidence; destructive actions need an explicit "yes".

**Personality:** charismatic 🎩 butler energy with motivational uplift — and since the latest build, a warm, natural companion who understands English, Urdu and Roman Urdu, replies in the language you use, and keeps spoken answers concise. Course codes (AMA, POM, RTT, AUD, GBM, PAK) are injected as voice-recognition hints because names are a known weak spot in speech.

---

## 2. The story so far — everything done, in order

### 2.1 v3 "Butler" and the audit (15 Nov 2026)

The v3 app (`database.py`, `tools.py`, `server.py`, `index.html`, `jarvis_academic.db`) could already slot study tasks by voice — on 15 Nov a spoken request ran `log_syllabus_and_schedule` and Jarvis confirmed day, window and location (episodic rows 47–50). It held **12 real tasks** (AMA Ch6 Mon 13:00, RTT Ch6 Tue 21:00, a GBM Ch13 presentation duplicated twice, nine unslotted legacy tasks…).

The audit read every file, opened the database, reproduced bugs on a scratch copy, and checked Google's Live API docs. It produced:

- **B1–B12** data-integrity bugs — e.g. **B1 (Critical):** syllabus inserts never committed, so the Syllabus Coverage Tracker could never fill; **B2 (High):** NULL-first sorting hid his two newest confirmed tasks from the prompt; **B5:** deadlines stored as words ("Wednesday") re-resolved against today, "tomorrow 9am" becoming 23:59.
- **R1–R9** real-time flaws — no session resumption/compression/GoAway handling (≈10-min connections, 15-min audio-only cap), silent kill paths, sync SQLite stalling audio, typed text interrupting speech, deprecated audio nodes, `innerHTML` injection risk, no auth, unpinned dependencies.
- **G1–G8** gaps — memory was the last 40 rows globally; nothing learned; identity hardcoded ("BBA 6th Semester" vs his "5th"); academics-only schema; no proactivity; the infamous **QP-at-risk** formula that ranked AMA below RTT while a B in AMA was *mathematically unreachable*; no self-model; no accountability ledger.
- **A1–A5** Android blockers — an APK can't contain the brain; the key must never ship inside it; battery/Doze realities.
- **Protocols P0–P10**, the target architecture, the memory/freshness engine (§5), the full data model (§6), core contracts (§7), Android topology (§8), the repo layout and **build queue M0–M7** (§9), extras (probe, undo/audit, kill switch, nag budget, cost guard, evals, backups — §10), and **decisions D1–D7** (§11).

### 2.2 The assessment & the registry (16 Nov, ~04:05–04:39 UTC — chat export "Project Audit and Next File")

A fresh agent instance received the audit + four code files, copied them into a scratch project, **booted the whole stack**, pushed a realistic wizard payload through `/api/setup`, called every registered tool, exercised ~45 data-layer functions and ran a synthetic v3→v4 migration. Findings:

- Project at the **M1→M2 boundary** (~40% of the 16-file queue through M3). M1 genuinely done: 65 tables, FKs on, FTS5 live, B1/B2/B3/B4/B9/B10/B12 closed; the B2 exit test passed (tasks 12 & 11 back on top).
- New blockers found: `tools.py` crashed on 2 of 9 tools (`get_working_state` NameError); `turns.role` CHECK would reject the live layer's first turn; the tool path still used the old broken deadline parser; `record_assessment` double-counted marks reported twice; the grade engine ignored `obtained` unless status was "Completed" and counted "Cancelled" rows in the denominator; the boot migration was never called.
- **Marks policy locked by the owner:** weighted points — **4 quizzes × 3.75 = 15, 4 assignments × 2.5 = 10, midterm 25, final 50 → 100 per course.**
- **Built `registry.py` (audit item #10)** — "the front desk": every tool described once as data; Gemini declarations, self-manifest, safety rules and plumbing all generated from it. **22 tools** (audit asked 17 — the handlers already existed), argument coercion ("90" → 90), range validation, destructive-tool confirmation tokens (3-min expiry), exception containment, full audit trail. **All exit tests green on the first run**, including: delete refused without permission then executed after "yes"; a mark reported twice stays one row; unknown tools politely rejected.
- Three disclosed out-of-file repairs: the missing import, registry load at boot, a read-only `/api/manifest`. **`.env.example` created** for AI Studio.
- Decisions taken by the owner: **audit order** (grade truth before voice) and **staged Android** (laptop → phone browser → APK).
- He installed the files, deleted the DB to start fresh → **500 `no such table: system_config`** (the server cached "schema ready" while the file vanished). This later became the **N7 self-heal**: every connection cheaply checks a sentinel and rebuilds the schema if the DB was deleted/replaced mid-run.

### 2.3 M2 complete and M3 built (16 Nov, sessions covered by `JARVIS_CONTEXT.txt` + `READ_ME_FIRST.txt`)

- **#11 `calculators.py`** — the accountant: margins, feasibility, `required_on_final`, allocation validation, GPA projection. 43 exit checks. The verdict Jarvis had once improvised is now computed: *AMA has 20 banked with only the 50-mark final left; a B needs 71; 71 − 20 = 51 > 50 → **B unreachable without quiz salvage.***
- **#12 academics end-to-end (M2 complete)** — the zip documented by `READ_ME_FIRST.txt`: a **real dashboard** (per-course grades with green/amber/red feasibility chips, GPA cards projected/floor/ceiling/target, today's blocks & tasks, due-in-48h, warnings), a **Wake-prompt button** (`/api/prompt`) showing the exact token-budgeted briefing packet, and the official exit test: **change a mark → dashboard AND wake prompt update immediately, no code edits.** 33 exit checks. Green rescue banner if old data was migrated.
- **M3 (items 13–16), built 16 Nov:**
  - **#13 `session_manager.py`** — the LiveSession state machine `IDLE→CONNECTING→LIVE→GOAWAY→RESUMING→LIVE→CLOSING`; real mode with AUDIO modalities, persona + wake packet as system instruction, registry tools with BLOCKING/NON_BLOCKING behaviour, session resumption (handle persisted), context-window compression (trigger 20 000 / target 12 000 tokens — lifts the 15-min audio-only cap), input+output transcription, auto-VAD, GoAway auto-resume ×3 with backoff, **exactly-once turn ledger** (UNIQUE `client_turn_id`), interrupted-partials flushed on close, `interaction_status` respected, tools off the audio loop via `asyncio.to_thread`, confirm gate bridging WS approve/deny → registry, usage telemetry, watchdog (90-min daily cap + 120 s idle close). **Rehearsal mode** (no key): a local understudy intent engine through the *same* registry/ledger/confirms — clearly labelled.
  - **#14 `text_stream.py`** — the split typed path (R5): streaming `generate_content`, same briefing + tools, ≤4 tool rounds, each tool executed exactly once, never touches the audio session.
  - **#15 `server.py`** — dotenv at import, auth middleware (R8), wizard/HUD routing, full CRUD (tasks w/ bulk+search+soft-cancel+undo, facts w/ supersede, assessments w/ upsert+undo, commitments), tools run/confirm, session history, settings whitelist, backups (traversal-guarded), `/api/probe`, and the `/ws/live` relay (binary PCM16 both ways + JSON control frames).
  - **#16 `static/hud.html`** — **HUD v4 "Night Desk"** (see the feature tour below).
  - **Requirements pinned (R9):** fastapi 0.163.1 · uvicorn[standard] 0.54.0 · google-genai 3.3.0 · python-dotenv 1.2.4 · websockets 17.4.
  - **Verification:** 61 server/WS checks (a full rehearsal conversation over a real WebSocket: tool calls, approval-gated delete, off-record, exactly-once ledger, idle auto-close, reconnect, auth 401/4401, probe, backups, traversal guard) + 37 HUD checks in jsdom against a live server (every panel, drawer-create, typed conversation, tool cards, mute, run-modal, required-final widget, live search, delete→confirm→undo, settings, probe — zero uncaught errors).
- **The real-key incident:** he installed M3, added his key, typed → `404 NOT_FOUND models/gemini-3.8 … v1`. Everything worked except the **audit-guessed model IDs** (AI Studio display names ≠ API IDs). Cure shipped: **`models.py`** — roster checker (`client.models.list()`), pure ranking `pick()`, `resolve/apply` with audit, **Auto-pick button** in Settings, and **404 self-heal on both paths** (text + live): on a 404 with nothing yielded yet, resolve the roster, save the pick, announce the switch in the transcript, retry once. Also fixed en route: `run_setup` 500 when the payload omitted university (NOT NULL). 23 hotfix checks; **197 total green**. Delivered as `jarvis_v4_model_fix.zip` (replace server/session_manager/text_stream/hud, add models.py).
- **But voice still 404'd** — at the WebSocket *handshake*, before any model name is sent. Session-2 context snapshot recorded the incident (§10) with a cure plan: upgrade the SDK first; if it persists, run a read-only `diagnose_live.py` that knocks on the SDK door, raw v1beta, raw v1alpha and REST.

### 2.4 The proof and the fix (16 Nov, 10:25–10:54 UTC — chat export "Project Context and API Issue")

- Full intake: 17 files across two uploads, **version-verified** (Auto-pick markers present → latest hotfix confirmed; a mystery attachment turned out to be a saved Google sign-in page — ignored).
- **The budget revolution:** the owner pointed out that re-reading ~360 KB of files every instance burned ~40% of his weekly limit. The instance responded by cataloguing files the cheap way (signatures + line numbers) and creating **`FILE_INDEX.md`** — a permanent map of every file's job, internal sections with line numbers, and "open when…" hints, plus a written protocol: *never read a whole file for context; jump to the exact lines.* Onboarding cost dropped ~10× (context + index ≈ 40 KB).
- **`diagnose_live.py` rebuilt** (4 doors, read-only, key never printed) and run by the owner on his laptop:
  - SDK live connect → **FAIL HTTP 404**
  - raw v1beta → **PASS, `setupComplete` in 344 ms**
  - raw v1alpha → **PASS in 369 ms**
  - REST models.list → **PASS, 28 models** (e.g. gemini-2.5-flash, gemini-2.5-pro, gemma-4-26b-a4b-it)
- **Root cause proven in the SDK's own source** (google-genai 3.3.0): `_api_client.py` L151 `GOOGLE_AI_API_DEFAULT_VERSION = 'v1'`; `live.py` L1044 reads `version = http_options.api_version` and L1069 builds `…/ws/google.ai.generativelanguage.{version}.GenerativeService.BidiGenerateContent` from it. Voice only lives at **v1beta/v1alpha** → the default `v1` door 404s. `_merge_live_http_options` passes client-level options through untouched, so a client override lands directly in the URL.
- **Fix applied:** `genai.Client(api_key=…, http_options=gt.HttpOptions(api_version="v1beta"))` in `session_manager._connect` and the probe's live client in `server.py`. Both files parsed clean; a fix zip was built (session_manager.py 32 758 B + server.py 32 184 B + READ_ME). `FILE_INDEX.md` line numbers refreshed.
- ⛔ **The session hit the usage limit** right after — before the context file was updated or delivery confirmed. The fix exists in that instance's workspace; whether the zip reached the laptop is unconfirmed.

### 2.5 The Kokoro voice build (between 16 and 18 Nov — a session not in the exports)

Another instance went *"round the garden and in through the kitchen"*: instead of fixing the Live door, it built an **alternative voice pipeline** and it was installed on the laptop:

**mic → `POST /api/stt` (Gemini transcribes) → text brain → reply split into sentences → `POST /api/tts` (Kokoro-82M speaking locally).**

- **Kokoro-82M** via `kokoro-onnx` — a local neural TTS, no API cost, **14 voices** including `bm_lewis` *"Lewis · Classic British Butler (Male)"*; default `af_heart`. `edge-tts` installed as a fallback. Pre-warmed at boot (`[tts] Kokoro-82M pre-warmed in RAM`).
- Sentence-by-sentence speech: he starts talking before he's finished thinking — the trick that makes it feel alive.
- HUD gained a **Local TTS Voice** selector beside the Live Voice selector; a streaming TTS queue.
- **Persona rewritten:** warm, gentle, uplifting natural companion; Urdu/Roman-Urdu; concise spoken replies; arithmetic rule (never improvise — call the tools); short course codes; never double-`add_task` on a reschedule; stale-state re-ask.
- **28 tools** now (health endpoint reports `tools: 28`).
- `models.py` ranking changed: **text prefers flash-lite** (free tier 500 RPD) over flash (20 RPD); excludes 2.0/2.5/transcribe/translate families from picks. `text_stream.py` now preserves full model Parts (incl. `thought_signature`) across tool rounds and falls back configured-model → 2.5-flash → 2.5-flash-lite on transient errors. Default Live voice became **Aoede**.
- ⚠️ **This branch never received the v1beta fix** — zero `http_options`/`v1beta` anywhere in it. The two branches diverged (full table in `PROJECT_PLAN.md` §2.2).

### 2.6 The intake, the landmines and the unfinished repair (18 Nov, 06:18–07:19 UTC — chat export "Previous working…")

The owner uploaded the laptop's live files (server 36 936 B, hud 116 334 B, database 101 513 B, …) plus `pip_freeze.txt`. The instance:

- Noticed **the files were newer than the context file** — discovered the whole Kokoro pipeline, confirmed the uploads were his live files (traceback line numbers matched exactly), and wrote `INTAKE_SESSION3.md` with **eight landmines (F1–F8)**. The three headlines:
  - **F1:** `requirements.txt` missing **python-multipart, kokoro-onnx, soundfile, edge-tts** — hand-installed on the laptop; *"your Oracle cloud box would arrive deaf and mute."*
  - **F3:** `/api/stt` **hardcodes `gemini-2.5-flash-lite`** — the exact guessed-ID species that caused the original 404, "wearing a fake moustache"; `models.py` exists to prevent this and STT ignores it.
  - **F4:** the **Browser-voice toggle disappears when a key is present but still gates all speech** — switch it off once in rehearsal, add the key → permanent silence with no visible switch. *(F2/F5–F8 details live only in `INTAKE_SESSION3.md`, which was not uploaded.)*
- The owner's full traceback + pip_freeze **proved the STT 500**: `AssertionError: The python-multipart library must be installed to use form parsing` at `server.py L743 → starlette requests.py L278` — and pip_freeze (59 packages) indeed has no python-multipart. Text and local TTS work because they use JSON, not multipart forms.
- **A second landmine behind the first:** the browser records `audio/webm`; Gemini accepts **WAV/MP3/AIFF/AAC/OGG/FLAC only** — fixing multipart alone would hand over a brand-new error. Both go in one build.
- Booted the app in the sandbox (rehearsal): `/api/health` → ok, schema v1, **28 tools**.
- **Benchmarked Kokoro on CPU** (the owner reported 3–4 s lag):
  - cold load **0.96 s**; real-time factor ≈ **0.8–0.97** (synthesis takes about as long as the audio itself)
  - 38-char clause ≈ 1.8 s synth; 74-char sentence ≈ 4.3–4.6 s; 140 chars ≈ 7.2 s
  - **threads=1 beats default** (3.47 s vs 3.99 s; threads=4 is worse: 7.8 s)
  - **phoneme batch size is the big knob:** maxph=510 → first chunk 3.48 s; **maxph=40 → first chunk 1.10 s** (3 chunks, same total audio)
  - warm short clauses: 0.74–1.6 s each
  → the cure: threads=1 + small phoneme batches + sentence streaming + the existing pre-warm ≈ **~1.1 s to first audio**.
- Began the consolidated fix: edited `server.py` ×3, `database.py` ×1, `session_manager.py` ×3 (the last edit failed on a stale match)…
- ⛔ **Usage limit hit again.** No zip, no delivery, no context update. **This is the work "not logged in context or readme"** — recovered here for the first time.

### 2.7 Consolidation (19 Nov — today)

All seven handoff files verified complete (non-empty, JSONs parse, message trees intact). The two audit copies are byte-identical duplicates. The three chats were extracted to readable transcripts (`extracted/`). This README and `PROJECT_PLAN.md` consolidate **everything above** into the single authoritative record the project's protocol demands.

---

## 3. What works today (and what honestly doesn't)

### ✅ Verified working
| Where | What |
|---|---|
| His laptop, real key | Typed conversation (streaming, tools, ≤4 rounds, self-heal on 404) |
| His laptop | Model roster REST + **Auto-pick** (28 models on his key; his 200-OK logs) |
| His laptop | **Local Kokoro TTS** — 14 voices, pre-warmed (but 3–4 s lag; cure benchmarked) |
| His laptop | Server boots; dashboard, tasks, facts, tools, settings, sessions, backups endpoints all 200 |
| His laptop | Raw v1beta **and** v1alpha Live handshakes open by hand (344/369 ms) — the door works, the SDK dialler doesn't |
| Sandbox | 197 distinct checks (see §16); full rehearsal conversation over a real WebSocket; HUD runtime in jsdom |
| Sandbox | Synthetic v3→v4 migration: 12 tasks imported, duplicate flagged, tasks 11 & 12 restored to prompt top, idempotent second run, integrity ok |

### ❌ Broken / unverified (honest list)
| Item | State |
|---|---|
| **Mic → STT on laptop** | 500 — missing `python-multipart` (proven). Behind it: webm format rejection (proven via docs) |
| **Gemini Live audio conversation** | Never executed anywhere with a real key. The v1beta fix is proven at source level and applied in the 16 Nov branch — **not merged into the laptop build** |
| **M3 exit test** | 30-min session / two reconnects / no lost turns / text <1 s / voice <3 s — still owed |
| **TTS latency** | 3–4 s to first audio; benchmarked cure not yet shipped |
| **F4 toggle trap, F3 hardcoded STT model, F1 requirements drift** | Catalogued, unfixed on the laptop |
| **F2, F5–F8** | Details only in the un-uploaded `INTAKE_SESSION3.md` |
| **v3 rescue on real data** | Green banner never confirmed by the owner — his 12 tasks' fate unverified |
| **18 Nov fixes** | Unfinished in a dead sandbox; nothing shipped |

---

## 4. Architecture in plain English

Think of Jarvis as a household:

- **The body (`server.py`, FastAPI)** — the front door and the switchboard. Serves the wizard and the HUD, exposes the read/write APIs, relays voice over a WebSocket, checks the access token at every door.
- **The memory (`database.py`, SQLite `butler.db`)** — the filing room. 65 tables (per the context record): identity, facts, rules, sessions, turns, summaries, state signals, courses, assessments, tasks, time blocks, commitments, outcomes, reviews, learned parameters, triggers, nudges, quiet hours, channels, tool specs, capabilities, proposals, audit log, migrations, eval runs… FTS5 search with LIKE fallback. One time rule: **store UTC ISO-8601, render Asia/Karachi.**
- **The brain (Gemini, AI Studio key)** — the thinking. Live API for real-time voice; `generate_content` for the typed path, summaries and transcription.
- **The voice box (`session_manager.py` + `text_stream.py` + STT/TTS)** — see §5.
- **The accountant (`calculators.py`)** — required-final, feasibility, GPA projection. The only thing allowed to do grade maths.
- **The front desk (`registry.py` + `tools.py`)** — 28 tools described as data; coercion, validation, confirmation tokens, audit; the model never touches the database directly.
- **The briefing officer (`assembler.py`)** — builds the wake packet under a token budget: always identity, academic truth, today's plan, open commitments, capabilities, state-age; optionally top facts (vault + proposed excluded, stale flagged), last session summary, ~6 raw turns (off-record excluded).
- **The roster clerk (`models.py`)** — asks the key which models actually exist, ranks them, applies picks, self-heals 404s. No guessed IDs, ever.
- **The face (`static/hud.html`)** — the Night Desk HUD.

```
 BROWSER (Night Desk HUD)
   mic ─ AudioWorklet PCM16 16k ─► WS /ws/live ─► LiveSession ─► Gemini Live (v1beta!)
   mic ─ MediaRecorder webm ─► POST /api/stt ─► Gemini transcribe ─► text brain
   typed ─► WS {type:text} ─► text_stream ─► Gemini generate_content (tools ≤4 rounds)
   replies ─► sentences ─► POST /api/tts ─► Kokoro-82M ─► WAV playback
   panels ◄─ /api/dashboard /tasks /facts /tools /settings /sessions /prompt /backup …
 SERVER (FastAPI, auth gate, boot rescue, registry.register_all)
 DATA (butler.db: memory · academics · planning · accountability · ops · FTS5)
```

---

## 5. The three voice modes

| Mode | Path | Needs | State |
|---|---|---|---|
| **Gemini Live ("true voice")** | Browser PCM16 → WS relay → Live API (AUDIO modalities, auto-VAD, barge-in, resumption, compression) → PCM16 24k back | Key + SDK + **v1beta override** | Door proven open by hand; SDK fix proven; merge into laptop build pending |
| **Kokoro pipeline ("kitchen door")** | Mic webm → `/api/stt` (Gemini transcribes) → text brain → sentence-split → `/api/tts` (Kokoro-82M local, 14 voices; edge-tts fallback) | Key (STT only) + local packages | TTS ✅ (lag cure benchmarked); STT ❌ (multipart + webm fixes pending) |
| **Rehearsal ("understudy")** | No key: local intent engine through the **same** registry/ledger/confirms; browser speech recognition + speechSynthesis, clearly labelled | Nothing | ✅ Fully working — every tool, confirmation and ledger entry is real; only the thinking is local |

Live-mode lifecycle: `IDLE → CONNECTING → LIVE → GOAWAY → RESUMING → LIVE → CLOSING`, with GoAway auto-resume ×3 backoff, resumption handles persisted (valid ~2 h), context compression (trigger 20 k / target 12 k tokens) lifting the 15-min audio-only cap, watchdog enforcing the 90-min daily cap and 120 s idle close. Typed text **never** interrupts speech (R5) — it rides the separate streaming path.

---

## 6. Feature tour

### The Night Desk HUD (`hud.html`)
**Art direction:** ink field, brass livery, parchment text, cyan for live, violet for memory; serif display + mono instruments; ambient lamp/aurora/grain/vignette layers; staged entrances, scroll reveals, reduced-motion support; custom brass scrollbars; mobile single-column ≤980 px, compact ≤640 px. **R7-safe: `textContent` only, zero raw-HTML injection.**

- **Voice stage** — audio-reactive canvas orb (single rAF, dpr≤2, visibility-paused); state-machine breadcrumb; session timer / turns / tokens / daily-cap meter; permanent control strip: **PTT hold**, hands-free, **off-record**, browser-voice, Live voice select, **Local TTS voice select**, interrupt, reconnect, end; streaming transcript (user / jarvis / tool / confirm cards with 3-min countdown, interrupted markers); typed composer. Master mute gates UI blips + playback + TTS.
- **Grades** — GPA cards (projected / floor / ceiling / target); course cards with bank/ceiling bars + **feasibility chips** (green secured · amber reachable · red unreachable); expandable **inline mark editing** (POST → recompute → undo); **required-final widget**.
- **Today** — task CRUD, search/filter/sort, drag-to-priority, bulk complete/cancel, detail drawer, confirm-modal deletes + **30 s undo toast**.
- **Memory** — facts CRUD with provenance/sensitivity/stale badges, supersede-on-edit; commitments keep/break.
- **Tools** — 28 cards, search + risk filter, schema-driven run modal, destructive → approval flow, JSON result viewer.
- **System** — capability **probe**; settings (sliders/selects → config rows; model fields become **roster dropdowns** when a key is present + **"Auto-pick models from my key"** button); session history → turns modal; **wake-prompt viewer** with budget meter + copy; backups.
- **Auth lock screen** — token stored in localStorage when `JARVIS_ACCESS_TOKEN` is set.
- **Setup wizard** (`setup.html`) — first boot: profile, domains, courses, assessments, grading scale, time blocks, routine anchors, goals, projects, people.
- **Legacy dashboard** (`dashboard.html`) at `/legacy`; the v3 `index.html` is a retired museum piece (says OFFLINE by design).

### Behind the panels
- **Wake packet on demand** (`/api/prompt`) — the exact briefing Jarvis wakes with, under an editable token budget (`context_token_budget`, default 4000, est. chars/4).
- **Change a mark → dashboard AND wake prompt update immediately** (the M2 exit test, passing).
- **Boot rescue** — `jarvis_academic.db` beside `butler.db` → backup-first, fingerprint-deduped migration: courses, fixed mark columns → assessment rows, tasks (slots/deadlines backfilled, duplicates flagged), turns, check-ins; unmapped columns salvaged as facts; green banner confirms.
- **Self-healing DB (N7)** — delete/replace `butler.db` mid-run and the next connection rebuilds the schema instead of crashing.
- **Undo + audit everywhere** — DB triggers write `undo_json`; API writes stash 30 s undo tokens; every write attributable.

---

## 7. The file map

Current essential set (his uploads arrive as `.txt` copies; sizes below are the **laptop Branch B** unless noted):

| File | Size | Role |
|---|---|---|
| `server.py` | 36,936 B | #15 body: auth, routing, CRUD, relay, probe, STT/TTS endpoints, Kokoro pre-warm. *(Branch A fixed copy: 32,184 B with v1beta probe client.)* |
| `session_manager.py` | 31,842 B | #13 LiveSession state machine, persona, rehearsal understudy, watchdog. *(Branch A fixed copy: 32,758 B with v1beta Live client.)* |
| `text_stream.py` | 6,399 B | #14 typed path; thought_signature preserved; fallback chain; ≤4 tool rounds |
| `models.py` | 4,112 B | Roster checker + Auto-pick + 404 self-heal; flash-lite text preference |
| `database.py` | 101,513 B | Data layer: 65 tables, FTS5, migrations, backups, clock, facts/freshness/episodic/state, academics writers, triggers, learned params, manifest data, N7 self-heal, `migrate_legacy_v3` |
| `registry.py` | 27,222 B | 28 tools as data; confirmation tokens (180 s); coercion; audit; upsert |
| `tools.py` | 12,859 B | Tool implementations (grade margins, dashboard state, slot finder, task ops…) |
| `calculators.py` | 17,997 B | #11 accountant: margins / feasibility / required_on_final / validate_allocations / gpa_projection |
| `assembler.py` | 13,524 B | #8 wake packet under token budget; `today_picture()` shared with the API |
| `static/hud.html` | 116,334 B | #16 Night Desk HUD v4 + Local TTS selector + streaming TTS queue |
| `static/setup.html` | 29,127 B | First-boot wizard |
| `static/dashboard.html` | 9,677 B | Legacy status dashboard at `/legacy` |
| `requirements.txt` | 183 B | Pinned R9 — **missing 4 voice packages (F1), fix pending** |
| `.env.example` | ~407 B | `GEMINI_API_KEY=` (empty → rehearsal) + `JARVIS_ACCESS_TOKEN=` (cloud) |
| `AUDIT.md` | 25,017 B | The full audit (keep with the project) |
| `diagnose_live.py` | ~7 KB | Read-only 4-door Live inspector for his laptop (never prints the key) |
| `DEPLOY.md`, `deploy/setup-oracle.sh` | — | Oracle Always Free guide + one-script setup (not yet re-uploaded) |
| `JARVIS_CONTEXT.md`, `FILE_INDEX.md` | — | The story + the map (session-3 versions not yet re-uploaded) |
| Sandbox-only | — | `exit_tests.py` (43), `exit_tests_12.py` (33), `exit_tests_13.py` (61), `exit_tests_models.py` (23), `seed_demo.py`, `hud_smoke.js` (37) — **never copy to the laptop; they wipe/fake DBs on purpose** |

Deliverable zips in the record: `jarvis_v4_M3.zip` (18 files, installed) → `jarvis_v4_model_fix.zip` (5 files, installed) → the 16 Nov live-fix zip (2 files + READ_ME, delivery unconfirmed) → item-11/item-12 zips (obsolete).

---

## 8. The data model & memory engine

**Memory layers (audit §5, implemented):**

| Layer | Holds | Lifetime | Tables |
|---|---|---|---|
| Working | current session context | session | RAM |
| Episodic | sessions, turns, summaries | raw 30 days, summaries kept | `sessions`, `turns`, `session_summaries` |
| Semantic | facts about you | until superseded | `facts` |
| Procedural | your rules & protocols | versioned | `rules` |
| State | energy, location, fed, stress | minutes–hours (TTL) | `state_signals` |

**Fact record:** domain, key, value, source (stated/observed/inferred), confidence, importance 1–5, created/last-verified timestamps, verify-after days, supersedes chain, sensitivity (normal/private/**vault**), status (active/**proposed**/retired). Inferred facts arrive as *proposed* and are confirmed by voice; nothing is overwritten — new values supersede old ones with history. **Vault never enters the prompt** unless the turn needs it.

**Freshness defaults (editable rows):** location ~hours (TTL 240 m) · energy/hunger/fed/stress ~3 h · schedule 30 d · goals 30 d · body 14 d · grades re-verify after each assessment · relationships 90 d · identity 180 d. A daily job ranks overdue facts by importance × staleness; the proactive engine will ask at most `fact_reask_cap` (3) per day, outside deep work. Answers reset `last_verified_at`.

**Retrieval:** SQLite FTS5 (`facts_fts`, `turns_fts`, `journal_fts`) with LIKE fallback; embeddings only if that proves insufficient. **Consolidation (nightly, planned M5):** summarise sessions, extract proposed facts, dedupe, decay, prep the weekly digest.

**Time rule:** UTC ISO-8601 + zone stored; Asia/Karachi rendered; `parse_ts()` reads every legacy format (B9 closed). `resolve_deadline()` understands today / tomorrow 9am / Nov 18 / next week / weekday==today (B5 closed).

**Safety nets:** FKs on, indexes, CHECK constraints, `schema_migrations`, connections closed on exceptions, `tx()` commits-or-rolls-back-and-closes, audit triggers with `undo_json`, `episodic_memory` kept as a view over the turn ledger for back-compat, backups with traversal-guarded download, N7 self-heal.

---

## 9. The grade engine & marks policy

**Policy (locked 16 Nov):** weighted points per course — **4 quizzes × 3.75 = 15 · 4 assignments × 2.5 = 10 · midterm 25 · final 50 → 100.** Assessments are *rows* (type, weight, max, obtained, expected, status), not fixed columns — so the maths has real inputs (G6 closed) and `record_assessment` upserts (reporting a mark twice never double-counts).

**The accountant computes, the model narrates:**
- **Bank / ceiling / projected** per course; letter + GP from the editable `grading_scale`.
- **Feasibility chips:** secured (green) / reachable (amber) / unreachable (red).
- **Required-on-final** per grade threshold. The canonical example: *AMA — 20 banked, only the 50-mark final left. B needs 71. 71 − 20 = 51 > 50 → **B unreachable without quiz salvage.*** This is the verdict Jarvis once improvised (audit G6, episodic rows 67–72); now it's arithmetic.
- **GPA projection** (projected / floor / ceiling / target cards) and allocation validation (allotments must sum to 100 — a wizard typo can't poison the maths).
- QP-at-risk demoted to a secondary column with its formula exposed; feasibility-driven ranking replaces it (planner v2 lands in M4).

Courses seen in the data: **AMA** (Adv Management Accounting, instructor Imran, 3 credits, tier 1, 35% time share, problem-drilling protocol, "hostile_qa" risk flag) and **RTT**; persona hints also list POM, AUD, GBM, PAK. Identity facts (semester 5 vs 6, targets) live in editable profile rows — one open question remains (OD3 in the plan).

---

## 10. The tool registry & safety system

**28 tools as data** (22 at birth → 26 at M3 → 28 today). Each spec: name, description, JSON-schema parameters, handler, **risk (read/write/destructive)**, confirmation policy, behaviour (BLOCKING/NON_BLOCKING) + scheduling (INTERRUPT/WHEN_IDLE/SILENT), domain. Gemini declarations are generated from specs at connect — spec-code drift (B6) is structurally impossible now.

**The front desk refuses to let the model misbehave:**
- `"90"` instead of `90` → coerced, not crashed.
- Missing/out-of-range argument → politely rejected, nothing written.
- **Destructive → confirmation token (180 s)**; the HUD shows an approval card with a 3-min countdown; "yes" bridges WS → `registry.confirm`; expiry cancels cleanly.
- A tool exception → caught, logged, session survives.
- Every write → `audit_log` (actor, before/after, undo), reversible.
- NON_BLOCKING tools run via `asyncio.to_thread` — audio never stalls (R3 closed); FunctionResponse scheduling per spec.

Families: grades & finals · tasks (add/update/delete/bulk/slot) · syllabus & lecture debriefs · study sessions & spaced reviews · check-ins & state · memory (remember/confirm/retire/recall) · commitments & outcomes · voice/settings · manifest & capabilities.

---

## 11. The Live-404 saga: diagnosis, proof, cure

**Symptom (his laptop, real key):** `Could not reach Gemini: server rejected WebSocket connection: HTTP 404` — persistent across model swaps *and* after Auto-pick, while REST (roster, autofix) returned 200.

**Why model swaps couldn't fix it:** the 404 happens at the **WebSocket handshake**, before the model name is sent (the model rides in the first `setup` message). The SDK was dialing the wrong door.

**The proof chain (16 Nov):**
1. `diagnose_live.py` on his machine: SDK → 404; **raw v1beta → `setupComplete` in 344 ms**; raw v1alpha → 369 ms; REST → 28 models. Key, network and model all innocent.
2. SDK source (google-genai 3.3.0): `_api_client.py` **L151** `GOOGLE_AI_API_DEFAULT_VERSION = 'v1'`; `live.py` **L1044** `version = http_options.api_version`; **L1069** `uri = f'{base_url}/ws/google.ai.generativelanguage.{version}.GenerativeService.{method}'`. Voice exists only at v1beta/v1alpha → default v1 = 404.
3. `_merge_live_http_options` returns the client's options untouched when no session options are given → a client-level override flows straight into the Live URL.

**The cure (verified at source level, applied in the 16 Nov branch):**
```python
self._client = genai.Client(
    api_key=self.api_key,
    http_options=gt.HttpOptions(api_version="v1beta"))
```
…mirrored on the probe's live client in `server.py`. Alternative no-code cure: `pip install -U google-genai` (newer SDKs target v1beta) — then re-pin requirements to the *reported working* version, never a guess.

**Status:** proven and applied in Branch A; **the laptop's Kokoro build (Branch B) does not contain it** — the merge is Phase 0 of the plan. The earlier model-ID 404s were a *different* bug, already cured by `models.py` roster + Auto-pick + self-heal.

---

## 12. The Kokoro voice build & its landmines

**The pipeline:** mic → `POST /api/stt` (Gemini transcribes) → text brain → sentence-split → `POST /api/tts` (**Kokoro-82M** local ONNX, 14 voices, pre-warmed; `edge-tts` fallback). No Live WebSocket needed — it walks around the locked door.

**The eight landmines (18 Nov intake; F2/F5–F8 details await `INTAKE_SESSION3.md`):**

| ID | Landmine | Status / cure |
|---|---|---|
| **F1** | requirements.txt missing `python-multipart`, `kokoro-onnx`, `soundfile`, `edge-tts` | Add + pin (laptop pip_freeze has kokoro-onnx 0.6.1, soundfile 0.14.0, edge-tts 7.3.1, onnxruntime 1.32.0, numpy 2.5.5, phonemizer 3.6.0, espeakng-loader 0.2.4 — **no python-multipart**) |
| **F2** | (speech-output territory — details in the intake doc) | Recover doc |
| **F3** | `/api/stt` hardcodes `gemini-2.5-flash-lite` | Roster-driven `stt_model` row |
| **F4** | Browser-voice toggle hidden in real mode but still gates all speech → permanent-silence trap | Keep toggle visible/truthful |
| **F5–F8** | Details in the intake doc | Recover doc |
| **+** | **STT 500** — Starlette asserts `python-multipart` at `req.form()` (server.py L743) | `pip install python-multipart` (one command) or dependency-free parsing |
| **+** | **webm rejected** — browser records `audio/webm`; Gemini accepts WAV/MP3/AIFF/AAC/OGG/FLAC | Server-side webm→WAV conversion |
| **+** | **TTS 3–4 s lag** | Benchmarked: **threads=1**, **maxph≈40** (first chunk 3.5 s → **1.1 s**), sentence streaming, pre-warm |

**Kokoro benchmark sheet (sandbox CPU, 18 Nov):** cold load 0.96 s · RTF 0.8–0.97 · 38-char clause ≈1.8 s · 74-char sentence ≈4.3–4.6 s · threads 1/2/4 → 3.47/3.92/7.78 s · maxph 510/120/80/60/40 → TTFC 3.48/3.52/1.78/1.75/**1.10** s · warm short clauses 0.74–1.6 s.

**The 18 Nov repair session** edited server.py ×3, database.py ×1, session_manager.py ×3 in its sandbox, then died at the usage limit — last edit failed, nothing shipped. Those edits are *not* on the laptop and *not* recoverable from the exports (diffs weren't recorded); Phase 0 re-does them properly on re-uploaded files.

---

## 13. Installation & running (laptop)

**Folder:** `~/Downloads/Butler3.0/` · Python 3.12 venv · single command.

```bash
cd ~/Downloads/Butler3.0
source venv/bin/activate
pip install -r requirements.txt        # after F1 is fixed this includes the voice packages
# one-command unblock for the mic (until the fixed requirements land):
pip install python-multipart
uvicorn server:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** → first boot: the setup wizard; afterwards: the Night Desk HUD. `/legacy` serves the old dashboard.

**`.env`** (copy `.env.example`, rename the copy to exactly `.env`):
```bash
# Get your key at https://aistudio.google.com/apikey
# RULES: never share this key, never paste it into chat, never ship it in an APK.
GEMINI_API_KEY=            # empty → rehearsal mode; filled → REAL mode
JARVIS_ACCESS_TOKEN=       # empty → local dev (boot warning); REQUIRED before cloud
```
The commented `GEMINI_LIVE_MODEL` line in older templates is inert — models are chosen in Settings (roster dropdowns / Auto-pick), not in `.env`.

**Keep `jarvis_academic.db` in the folder** until the green rescue banner confirms the 12 v3 tasks came home. Boot banner tells you the mode: `REAL mode — Gemini key present, SDK 3.3.0` vs `REHEARSAL mode`.

**Kokoro model files** (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) must sit where the server expects them (they're already on the laptop; the cloud script must provision them too).

**Never copy** `exit_tests*.py`, `seed_demo.py`, `hud_smoke.js` to the laptop — sandbox-only, they wipe/fake databases on purpose.

**Applying a fix zip:** replace exactly the files listed in its READ_ME, restart uvicorn, run the one consolidated test. Current replace-lists in the record: M3 zip (18 files) → model_fix zip (server, session_manager, text_stream, hud + add models.py) → live-fix zip (server + session_manager). **The next zip (Phase 0) supersedes all of them on the Branch B base.**

---

## 14. Configuration reference

All rows in `system_config`, editable via HUD → System → Settings (whitelisted) or by talking (once the settings tools are used). Defaults at seed:

| Key | Default | Meaning |
|---|---|---|
| `setup_complete` | false | wizard gate |
| `voice_name` | Fenrir *(seed)* / Aoede *(live-session default in current build)* | Gemini Live voice |
| `butler_name` / `personality` | Butler / Formal Butler | legacy persona rows (persona text now lives in session_manager) |
| `live_model` | gemini-3.8-live | voice model — **always confirm via roster/Auto-pick** |
| `text_model` | gemini-3.8 *(seed guess)* | typed model — Auto-pick resolves a real ID; current ranking prefers flash-lite |
| `live_minute_cap` | 90 | daily Live-minutes cost guard (raisable — his Live quota is effectively unlimited: 65K TPM, his peak ≈6%) |
| `session_idle_close_secs` | 120 | idle auto-close |
| `context_token_budget` | 4000 | wake-packet budget (est. chars/4) |
| `accountability_academics` / `_other` | L2 / L1 | ladder intensity (D4) — Jarvis can never raise it |
| `quiet_start` / `quiet_end` | 23:00 / 07:00 | quiet hours |
| `nag_budget` | 5 | max nudges/day |
| `fact_reask_cap` | 3 | max freshness re-asks/day |
| `rollover_cap` | 45 | missed-block rollover minutes cap |
| `wake_word_pref` | Push-to-Talk | D5 |
| `privacy` | ask_sensitive | sensitivity handling |
| `raw_turn_retention_days` | 30 | episodic raw retention |

Local TTS voice is a HUD localStorage preference (`jarvis_local_voice`, default `af_heart`); mute, browser-voice and token also live in localStorage.

---

## 15. Troubleshooting — every error seen so far

| Error seen | Cause | Cure |
|---|---|---|
| `500 … no such table: system_config` after deleting the DB mid-run | schema-ready cache vs vanished file | **Fixed (N7 self-heal)** — restart also heals; keep current database.py |
| `404 NOT_FOUND models/gemini-3.8 … v1` on typed | audit-guessed model IDs ≠ API IDs | **Fixed** — models.py roster + Auto-pick + 404 self-heal; just type, or Settings → Auto-pick |
| `Could not reach Gemini: server rejected WebSocket connection: HTTP 404` (voice, persistent) | SDK 3.3.0 defaults Live URL to v1 | **v1beta override** (§11) — port into the current build, or `pip install -U google-genai` then re-pin |
| `POST /api/stt 500 … AssertionError: The python-multipart library must be installed to use form parsing` | package missing from venv | `pip install python-multipart` (and fix requirements — F1) |
| STT fails *after* multipart fixed | browser `audio/webm` not accepted by Gemini | server-side webm→WAV conversion (Phase 0 build) |
| "voice transcription failed" with no 500 | check key/model for STT (F3 hardcoded lite) | roster-driven STT model (Phase 0) |
| Jarvis permanently silent after adding key | F4 toggle trap | keep Browser-voice toggle visible/on; fix in Phase 0 |
| Local TTS 3–4 s lag | Kokoro RTF≈0.9, whole-sentence synth, default threads | threads=1 + maxph≈40 + sentence streaming (benchmarked, Phase 0) |
| `run_setup` 500 when university omitted | NOT NULL columns | **Fixed** — coalesced to '' |
| `gaierror(-3, Temporary failure in name resolution)` once | transient DNS hiccup | resolved itself (autofix succeeded after) |
| Daily text wall (~6 tool-heavy questions) | 3.8 Flash free tier 20 RPD | flip Settings dropdown to flash-lite (500 RPD) |
| Old tasks missing from prompt | B2 NULL-first sorting | **Fixed** — tasks_for_prompt sorts NULL slots last; migration restores tasks 11 & 12 |
| Marks double-counted when reported twice | INSERT-only assessments | **Fixed (N6 upsert)** |
| HUD shows OFFLINE on `index.html` | retired v3 page | use `/` (Night Desk); index.html is a museum piece |
| Mic blocked in a sandbox preview iframe | iframe permissions | typed path always works; on laptop/HTTPS mic works |

**Diagnostic kit:** `python diagnose_live.py` (4 doors, read-only, key hidden) · System → **Run probe** (SDK/integrity/coverage/assembler/roster; real mode: models.get both IDs, text ping ms, Live handshake ms, Roman Urdu ping; saved to capabilities) · `uvicorn … 2>&1 | tee jarvis_log.txt` for full tracebacks (the *last line* is the diagnosis) · `pip freeze > pip_freeze.txt` for dependency truth · DB logs: export the last ~100 audit/turn rows as text from the terminal (direct `.db` upload and JSON conversion both hit parsing errors).

---

## 16. Testing & verification

**The 197-check ledger (sandbox, all green):**
- **43** — item #11 calculators (margins, feasibility, required-final, allocations, GPA).
- **33** — item #12 academics end-to-end (dashboard, wake prompt, mark→prompt reactivity, rescue).
- **61** — M3 server/WS: full rehearsal conversation over a real WebSocket incl. tool calls, approval-gated delete, off-record, exactly-once ledger, idle auto-close, reconnect, auth 401/4401, probe, backups, traversal guard.
- **37** — HUD runtime in jsdom against a live server: all panels render, drawer-create, typed conversation, tool cards, mute, run-modal, final widget, live search, delete→confirm→undo, settings, probe; zero uncaught errors.
- **23** — model hotfix: ranking, resolve/apply/audit, stubbed-404 text & live self-heal, endpoints, probe, rehearsal regression.

SDK surface introspected against google-genai 3.3.0 (LiveConnectConfig fields, Behavior/FunctionResponseScheduling enums, AsyncSession methods, GoAway/resumption/interaction_status). Migration tested on a synthetic copy of the real DB (12 tasks, dedupe, idempotency, integrity ok). Kokoro benchmarked on CPU (§12). The 18 Nov instance also booted the laptop build in rehearsal (28 tools, schema v1).

**Still owed (needs his key/machine):** real Gemini Live audio · the **M3 exit test** (30 min, two reconnects, no lost turns, text first token <1 s, voice start <3 s) · STT round-trip · the rescue-banner confirmation. **Real Gemini audio has never been executed from a sandbox — no instance may claim it verified.**

**Sandbox-only harnesses** (never ship): `exit_tests.py`, `exit_tests_12.py`, `exit_tests_13.py`, `exit_tests_models.py`, `seed_demo.py`, `hud_smoke.js` (needs jsdom + server on 8011).

---

## 17. Security & privacy

- **API key:** only in `.env`; never in chat, code, logs or APK; `diagnose_live.py` never prints it; uploads use `.env.example`.
- **Access token (R8):** `JARVIS_ACCESS_TOKEN` enforced on all `/api/*` + `/ws/*` (header `X-Jarvis-Token` or `?token=`; constant-time compare; WS close **4401**, origin mismatch **4403**). Unset = local dev mode + boot warning. **Must be set before any cloud exposure** — the deploy script generates it. HUD lock screen stores it in localStorage.
- **Destructive actions:** explicit confirmation with 180 s tokens; every write audited with undo; 30 s undo ledger on API deletes.
- **Privacy modes:** off-record turns (never stored, excluded from the wake packet), master mute, mic kill, **vault-tier facts never in prompts**, sensitivity badges, `ask_sensitive` default.
- **Injection hygiene (R7):** HUD renders via `textContent` only — model-written strings never become HTML.
- **Backups:** manual + nightly (cloud), traversal-guarded downloads, backup-first migrations; restore drill planned (M6).
- **Android (future):** key never in APK — Keystore-backed phase-1 shortcut, then ephemeral tokens (Preview: 1 min start / 30 min send, locked to model+config so the system prompt stays server-side).

---

## 18. Cloud deployment (planned next)

**Decision D1 (delegated to the agent, decided 16 Nov): Oracle Cloud Always Free ARM** — 2 OCPU / 12 GB Ubuntu 24.04, 200 GB free disk, **10 TB/month free egress** (voice ≈170 MB/hour; GCP's free ~1 GB egress would bill after a few hours). June-2026 Oracle update: 3,000 OCPU-hours + 18,000 GB-hours/month → 24/7 use ≈1,460/8,760, safely inside (verified against oracle.com/cloud/free + the limits update; re-verify at build time).

**Kit:** `DEPLOY.md` + `deploy/setup-oracle.sh` — Caddy auto-HTTPS via `{ip}.sslip.io`, systemd service, token generation, nightly backup cron. **Fallbacks:** any ~$4 Ubuntu VPS runs the same script; GCP e2-micro with the egress caveat.

**Order (his rule):** real voice on the laptop FIRST, then cloud. The cloud box needs the completed requirements (F1) plus the Kokoro model files, or it arrives deaf and mute. After deployment his **phone browser works laptop-free** — the staged Android route's middle step (a free Tailscale tunnel is the interim stopgap).

---

## 19. Roadmap

| Phase | What | Done when |
|---|---|---|
| **Phase 0 — voice consolidation** (next) | Merge branches: STT multipart+webm→WAV+roster model · TTS threads=1/maxph≈40/streaming · F4 toggle · v1beta Live port · complete requirements · recover F2/F5–F8 | One laptop session: typed ✅ mic ✅ speech ✅ Live ✅ probe green; M3 exit test reported; rescue banner answered |
| **Phase 1 — cloud** | Oracle Always Free + DEPLOY script + token + TLS + backups | Phone browser works with the laptop closed; restore drill passes |
| **M4 — planner + proactive** | Constraint scoring (duration, deadline slack, energy, location, dependencies, load cap); triggers (pre-block, block-end outcome, T-24h/T-3h, lecture debrief, stale state/fact, missed-block replan, reviews, patterns); channels; quiet hours; ladder L1–L4 | A missed block triggers an unprompted replan offer; 20 scheduling cases pass |
| **M5 — learning** | Capture→aggregate→propose→apply→monitor→rollback; adherence, estimation error, velocity, energy-by-hour; weekly review | A learned parameter changes a plan and rolls back; weekly report shows adherence + estimation error |
| **M6 — evals & ops** | Golden dialogues (slotting, stale-state re-ask, interrupted-turn resume, gates); migrations/backups/logs | Evals gate every change |
| **M7 — Android** | Kotlin+Compose thin shell; PTT + notification action first, wake word later; ephemeral tokens → direct Live; relay fallback; 48 h offline nudge cache; foreground service only during sessions | Same conversation, same database, phone and laptop |

Life-domain packs (fitness, projects/work, finance, journal, habits) follow the academics pack pattern: manifest + schema + calculators + prompts, no core edits (P3).

---

## 20. Project protocols — how instances hand off

**The owner's standing rules (every instance inherits these):**
- Not a programmer, self-described forgetful → **plain English, gentle reminders, exact file lists** with every delivery (which files to download/replace).
- Free-tier → **one consolidated test run per build**, minimal searches, no overthinking on his dime.
- Charismatic 🎩 butler energy, motivational uplift; no stress — he does not judge.
- One milestone at a time in audit order; **disclose out-of-file repairs; surface decisions** instead of guessing.
- **Never ask for his API key in chat.** Key only in `.env`.
- **Keep `JARVIS_CONTEXT.md` updated every session** — and `FILE_INDEX.md` after edits (line numbers shift).

**The agent protocol (§0 of the context file):** before writing ANY code, verify the workspace has current copies of the essential files; if missing or stale, **ask him to attach them FIRST — never reconstruct from memory** (it causes inconsistency errors between instances). Uploads arrive as `.txt` copies; rename and version-check them (Auto-pick markers, v1beta string, tool count) before trusting.

**The budget protocol (born 16 Nov after a 40%-of-weekly-limit intake):** onboard via `JARVIS_CONTEXT.md` + `FILE_INDEX.md` (~40 KB) instead of full reads (~360 KB); jump to exact line ranges; catalogue new files by signatures + line numbers; refresh the index with one command after edits.

**The lesson of three dead sessions (16 Nov ×2, 18 Nov ×1):** all three hit the usage limit *after* doing work but *before* saving the record. **Write docs early; ship them with the zip; never end a session with unlogged work.** This README + `PROJECT_PLAN.md` exist because that rule was broken.

**Sandbox realities:** venvs and background processes outside the workspace don't persist between instances; system python may lack pip (bootstrap via get-pip.py + urllib); `zip`/`curl` may be absent (use python zipfile/urllib); node v20 present; jsdom must be reinstalled. Rebuild before testing; the preview server binds 0.0.0.0:8000 ("Jarvis Night Desk"); mic may be permission-blocked in the preview iframe — typed always works.

---

## 21. Workspace contents

```
uploads/                          ← the seven handoff files (all verified complete 19 Nov)
  AUDIT.txt                       25,017 B  full audit (15 Nov)
  Jarvis v4 — Audit, … .txt       25,017 B  byte-identical duplicate of AUDIT.txt
  JARVIS_CONTEXT.txt              21,591 B  context snapshot (16 Nov, session 2)
  READ_ME_FIRST.txt                2,175 B  item-#12 / M2-complete zip readme
  chat-export-1794804134044.json 697,042 B  chat A (16 Nov 04:05) — 8 messages
  chat-export-1794826550346.json 964,814 B  chat B (16 Nov 10:25) — 6 messages
  chat-export_Previous working…  913,228 B  chat C (18 Nov 06:18) — 4 messages + empty stub
extracted/                        ← readable transcripts mined from the exports
PROJECT_PLAN.md                   ← consolidated forward plan (phases, exit tests, risks)
README.md                         ← this file
```

**Referenced but not yet uploaded** (needed for the next *code* session, not for these docs): the Branch B laptop code files, `INTAKE_SESSION3.md` (F2/F5–F8), `FILE_INDEX.md`, the session-3 `JARVIS_CONTEXT.md` (28,256 B), `diagnose_live.py`, `DEPLOY.md`, `deploy/setup-oracle.sh`, the three deliverable zips, and a text export of the last ~100 DB log rows.

---

## 22. FAQ for the owner

**Did it crash because there was no `.env`?** No. The `system_config` 500 was the deleted-DB race (fixed by self-heal); the STT 500 is a missing package. `.env` only decides REAL vs rehearsal mode.

**Are you asking me to paste my key?** Never. The key lives in `.env` on your machine. Upload `.env.example` (the blank template) if a file is needed.

**Is everything uploaded?** The seven handoff files: yes, all complete. The *code* files for the next build: not yet — the exact request list is in `PROJECT_PLAN.md` §6 Step 0.

**When can I talk to Jarvis on my laptop?** After Phase 0 (one consolidated build + one test run). Typing and the local voice already work; the mic needs the one-command unblock plus the webm fix; the true Live voice needs the two-line v1beta port.

**When Android?** After cloud (Phase 1). Staged route: laptop voice → phone browser against the cloud brain → Kotlin APK at M7. The APK never contains your key.

**Will voice cost money?** Your AI Studio Live quota is effectively unlimited (65K tokens/min; your peak used ≈6%). The 90-min/day cap is a guardrail you can raise. Text on 3.8 Flash is the scarce one (20 requests/day) — flash-lite gives 500. Kokoro TTS is local and free. Oracle's free tier covers the always-on box (10 TB egress/month).

**What happened to my 12 old tasks?** The rescue migration is written, backup-first and tested on a copy — but only *you* can confirm it on the real data: look for the **green banner** on the dashboard. Keep `jarvis_academic.db` in the folder until you see it.

**Why do sessions keep dying mid-work?** Free-tier usage limits. The countermeasure is now protocol: docs-first, small shippable increments, and this consolidated record so any new instance resumes in minutes, not hours.

---

## 23. Glossary

| Term | Meaning |
|---|---|
| **HUD / Night Desk** | the browser face of Jarvis (hud.html) |
| **Wake packet / briefing** | the token-budgeted context Jarvis wakes with (assembler.py) |
| **Rehearsal mode** | no-key understudy: real tools/ledger/confirms, local thinking |
| **REAL mode** | key present; Gemini thinks and (Live) speaks |
| **Live API** | Gemini's real-time bidirectional voice WebSocket (v1beta door) |
| **GoAway / resumption** | Google's ~10-min connection warning + the handle that continues the session (valid ~2 h) |
| **Compression** | context-window sliding window that lifts the 15-min audio-only cap |
| **Kokoro-82M** | local neural TTS (ONNX); 14 voices; "Lewis · Classic British Butler" |
| **STT / TTS** | speech-to-text / text-to-speech |
| **PTT** | push-to-talk (hold the brass button) |
| **Roster / Auto-pick** | models.py asking your key which models exist and choosing sane ones |
| **Self-heal (404)** | on a model 404, resolve roster → save → announce → retry once |
| **N7 self-heal (DB)** | rebuild schema automatically if butler.db vanishes mid-run |
| **Feasibility chip** | green secured / amber reachable / red unreachable grade verdict |
| **Required-final** | marks needed on the final for each grade threshold |
| **QP-at-risk** | the old flawed ranking formula, demoted by the audit |
| **Ladder L1–L4** | accountability intensity: nudge → ask why → renegotiate → log+review |
| **Vault** | sensitivity tier never placed in prompts uninvited |
| **Off-record** | turns never stored, excluded from memory |
| **Ephemeral token** | short-lived client token for direct-to-Gemini Android sessions |
| **Branch A / Branch B** | the 16 Nov fixed lineage vs the laptop's Kokoro lineage (plan §2.2) |
| **F1–F8** | the eight landmines found in the Kokoro build (18 Nov intake) |
| **B/R/G/A ids** | audit findings: bugs / real-time / gaps / Android |
| **M0–M7** | the audit's milestone build queue |
| **PKT** | Pakistan Standard Time, UTC+5 — the render timezone |

---

*Built with truth before voice. 🎩 The butler's voice will be heard.*
