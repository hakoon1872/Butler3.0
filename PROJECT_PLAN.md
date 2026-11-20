# JARVIS v4 — Consolidated Project Plan

**Prepared:** 19 Nov 2026 · **Owner:** Mehtab Haider (Lahore, PKT = UTC+5)
**Status at a glance:** M0–M3 built · typed path + local TTS working on the laptop · voice input (STT) broken by a missing package · Gemini Live door root-caused and fix proven, but not yet merged into the laptop's current build · cloud + Android ahead.
**Sources consolidated:** `AUDIT.txt` (= `Jarvis v4 — Audit, Improvement Protocols & Framework.txt`, byte-identical), `JARVIS_CONTEXT.txt` (16 Nov, session-2 snapshot), `READ_ME_FIRST.txt` (item-#12 / M2 zip readme), and three full chat exports — 16 Nov 04:05 "Project Audit and Next File", 16 Nov 10:25 "Project Context and API Issue", 18 Nov 06:18 "Ambitious Tech Project Context" (the session whose work was never logged into the context file or readme).

> **Plain-English summary.** Jarvis — your voice-powered academic butler — is roughly 60% built. The body (database, tools, grade maths, dashboard, memory, safety) is done and tested. The voice is *almost* there: typing works, the local butler voice (Kokoro) works but is slow, and the microphone path crashes because one small Python package is missing. Meanwhile, the "true voice" door (Gemini Live) was diagnosed down to the exact line of Google's SDK and a fix was proven — but that fix lives in an older branch of the code, not in the build currently on your laptop. **Phase 0 of this plan merges the two branches and makes the whole voice loop work in one sitting.** Then: cloud, planner, learning, evals, Android — in that order.

---

## 1. Vision & product definition

**Jarvis** is a voice-first AI life butler, starting with academics (BBA semester courses) and expanding to fitness, projects, finance, journal and habits. FastAPI + SQLite body, Gemini brain (Google AI Studio key), browser "Night Desk" HUD today, always-on cloud brain + thin Android shell tomorrow. Charismatic 🎩 butler energy, plain English, motivational uplift, Urdu / Roman-Urdu aware.

**The four rules every design decision obeys (from the audit):**
1. **The LLM proposes, code disposes.** Arithmetic, dates, scheduling and grade maths run in tested code; the model decides intent and speaks.
2. **Data, not code.** Anything changeable by talking (targets, rules, routines, metrics, model IDs) is a database row, not a constant.
3. **Every memory has provenance and freshness.** Who said it, when confirmed, when to re-ask. Vault-tier facts never enter prompts uninvited.
4. **Haider approves what changes Jarvis.** Learned parameters and rule changes arrive as proposals; destructive actions need an explicit "yes".

**Success picture:** Jarvis wakes with a truthful briefing (grades, plan, promises), hears you, answers with real numbers, acts through 28 audited tools, remembers across weeks, nudges you unprompted within a nag budget, runs 24/7 in the cloud without your laptop, and eventually lives on your phone.

---

## 2. Current state assessment

### 2.1 Milestone scoreboard (audit build queue)

| Milestone | Scope | Status | Evidence |
|---|---|---|---|
| **M0** Hotfix v3 | B1–B3, B5–B8, R2/R3 patches + v3→v4 migration | ✅ Absorbed into the v4 rewrite | `database.py` header lists every closure; migration tested on a synthetic copy of the real DB (12 tasks imported, 1 duplicate flagged, tasks 11 & 12 back on top of the prompt) |
| **M1** Core & memory (items 3–7) | db + migration 0001, clock, facts, freshness, episodic, state | ✅ Done | 65 tables, FKs on, FTS5 live, UTC-ISO storage / PKT rendering, layered memory |
| **#8** Assembler | Token-budgeted wake packet | ✅ Done | `assembler.py`, `context_token_budget` row (default 4000), `/api/prompt` viewer |
| **#9** Manifest | Jarvis self-model | ✅ Done | `tool_specs` + `capabilities` + `proposals`; `/api/manifest` |
| **#10** Registry | Declarative tools, confirmation, coercion, audit | ✅ Done 16 Nov (chat A) | Built with 22 tools in one pass, all exit tests green first run; grew to 26 (M3) → **28** (current laptop build) |
| **#11** Calculators | Required-final, feasibility, GPA projection | ✅ Done | 43 exit checks; AMA "B unreachable" verdict computed, not improvised |
| **#12** Academics end-to-end (M2 complete) | Dashboard + wake prompt react to mark changes | ✅ Done | 33 exit checks; official exit test (change a mark → dashboard AND wake prompt update, no code edit) passes |
| **M3** Live layer (items 13–16) | session_manager, text_stream, server+relay+auth+pins, HUD v4 | ✅ Built 16 Nov | 61 server/WS checks + 37 HUD jsdom checks; **real-key voice exit test still pending** |
| **Model-roster hotfix** | models.py roster + Auto-pick + 404 self-heal | ✅ Shipped & confirmed on his key | 23 extra checks; his 200-OK logs prove REST roster + Auto-pick work |
| **Live-404 root-cause fix** | `api_version="v1beta"` override | ✅ Proven & applied **in the 16 Nov session-3 branch** — ⚠️ NOT in the laptop's current build | diagnose_live.py verdict + SDK source proof (§5.2) |
| **Kokoro voice build** (STT → text → local TTS) | Alternative voice pipeline | ⚠️ Installed on laptop, partially working | typed ✅, local TTS ✅ (3–4 s lag), STT ❌ (500), 8 landmines catalogued |
| **18 Nov repair session** | Fix STT/TTS landmines | ⛔ Interrupted by usage limit mid-edit | edits begun in sandbox (server.py ×3, database.py ×1, session_manager.py ×3, last edit failed); **nothing delivered, nothing logged** |
| **M4** Planner + proactive | Constraint scoring, triggers, ladder | ⬜ Not started | schema + trigger rows already seeded |
| **M5** Learning | Aggregates, learned params, weekly review | ⬜ Not started | `learned_params`, `outcomes`, `reviews` tables exist with writers |
| **M6** Evals & ops | Golden dialogues, backups, logs gating | ⬜ Not started | `eval_runs` table + backup endpoints exist |
| **M7** Android | Kotlin shell, ephemeral tokens, offline cache | ⬜ Not started | topology decided (D2/D3/D5) |

### 2.2 ⚠️ The two-branch problem (the single most important integration fact)

The uploads reveal **two divergent code lineages**, and the authoritative context file only knows about one of them:

| File | Branch A — 16 Nov session-3 (chat B workspace) | Branch B — laptop today (18 Nov uploads) | Notes |
|---|---|---|---|
| `server.py` | 32,184 B (M3 + roster hotfix + **v1beta probe fix**) | 36,936 B (M3 + roster hotfix + **STT/TTS endpoints**, no v1beta) | B adds `/api/stt`, `/api/tts`, `/api/tts/voices`, Kokoro pre-warm |
| `session_manager.py` | 32,758 B (**v1beta Live fix at ~L218**) | 31,842 B (persona rewrite, Aoede default, **no v1beta**) | zero `http_options`/`v1beta` hits anywhere in Branch B |
| `hud.html` | 106,918 B | 116,334 B | B adds Local TTS Voice selector + sentence-streaming TTS queue |
| `models.py` | 5,132 B (prefer newest 3.x flash for text) | 4,112 B (**prefer flash-lite for text** — 500 RPD vs 20; excludes 2.0/2.5/transcribe/translate) | ranking policy changed between branches |
| `text_stream.py` | 5,361 B | 6,399 B | B preserves `thought_signature` parts; adds model fallback chain (configured → 2.5-flash → 2.5-flash-lite) |
| `database.py` | 99,630 B | 101,513 B | B slightly evolved |
| `registry.py` / `tools.py` | 26,520 B / 11,862 B (26 tools) | 27,222 B / 12,859 B (**28 tools**) | B grew the toolset |
| Persona | Formal butler | Warm companion; Urdu/Roman-Urdu; course-code hints (AMA, POM, RTT, AUD, GBM, PAK); never double-`add_task` | B is the persona he last heard |

**Consequence:** installing Branch A's fix zip over Branch B would *regress* the Kokoro pipeline, persona and 28-tool registry. Installing nothing leaves the Live door broken and STT crashing. **Phase 0 therefore merges: Branch B is the base; Branch A's v1beta override (2 small edits) is ported into it.** Never reconstruct either branch from memory — re-upload first (§9).

### 2.3 Honest verification ledger

**Verified (sandbox, 197 distinct checks green):** 43 (item 11) + 33 (item 12) + 61 (M3 server/WS: full rehearsal conversation over a real WebSocket incl. tool calls, approval-gated delete, off-record, exactly-once ledger, idle auto-close, reconnect, auth 401/4401, probe, backups, traversal guard) + 37 (HUD runtime in jsdom vs live server, zero uncaught errors) + 23 (hotfix: ranking, resolve/apply/audit, stubbed-404 self-heal on both paths, endpoints, rehearsal regression). SDK surface introspected against google-genai 3.3.0.

**Verified on his real key / laptop:** typed path ✅ · roster REST + Auto-pick ✅ (his 200 logs) · raw v1beta + v1alpha Live handshakes ✅ (344/369 ms, `setupComplete`) · 28 models visible ✅ · local Kokoro TTS ✅ (200s, but 3–4 s lag) · server boots ✅.

**NOT yet verified anywhere:** real Gemini Live audio conversation · the M3 exit test (30-min session, two reconnects, no lost turns, text first token < 1 s, voice start < 3 s) · STT on his laptop (currently 500) · the v3 rescue green banner on his real 12 tasks (still unconfirmed — ask him) · the 18 Nov fixes (never finished or shipped).

### 2.4 Active blockers & defects (priority order)

| # | Issue | Root cause (proven) | Fix | Where fixed today |
|---|---|---|---|---|
| 1 | **STT 500** — "voice transcription failed" | `python-multipart` absent from the venv (pip_freeze confirms); Starlette asserts on `req.form()` at server.py L743 | `pip install python-multipart` **or** dependency-free multipart parsing in `/api/stt` | laptop: one command; build: either |
| 2 | **Webm audio rejected** (hides behind #1) | Browser MediaRecorder produces `audio/webm`; Gemini accepts WAV/MP3/AIFF/AAC/OGG/FLAC only | Server-side webm→WAV conversion before the STT call | 18 Nov session began this; must be re-done & shipped |
| 3 | **TTS 3–4 s lag** | Kokoro RTF ≈ 0.8–0.97 on CPU; whole sentences synthesized before playback; default threads | Benchmarked knobs: **threads=1**, phoneme batch **maxph≈40** (TTFC 3.5 s → **1.1 s**), sentence/clause streaming, pre-warm (already present) | 18 Nov benchmarks done; apply to `/api/tts` + HUD queue |
| 4 | **Gemini Live WS 404** | SDK 3.3.0 defaults `api_version='v1'` (`_api_client.py` L151) and builds the Live URL from it (`live.py` L1069); voice only lives at v1beta/v1alpha | `genai.Client(api_key=…, http_options=gt.HttpOptions(api_version="v1beta"))` on the Live client (+ probe client) | proven & applied in Branch A; **port to Branch B** |
| 5 | **F3 — STT model hardcoded** `gemini-2.5-flash-lite` | Same guessed-ID species that caused the original 404 | Route STT through `models.py` roster / a `stt_model` config row | to do |
| 6 | **F4 — Browser-voice toggle trap** | Toggle hidden when key present, yet still gates all speech → permanent silence with no visible switch | Keep the toggle visible/effective in real mode (or decouple local TTS from it) | to do |
| 7 | **F1 — requirements drift** | `requirements.txt` missing `python-multipart`, `kokoro-onnx`, `soundfile`, `edge-tts` (hand-installed on laptop) | Complete + re-pin requirements; cloud box would otherwise arrive deaf & mute | to do |
| 8 | F2, F5–F8 (remaining landmines) | Details live only in `INTAKE_SESSION3.md` (18 Nov instance) — **not uploaded** | Re-upload that file, or re-audit the voice pipeline in Phase 0 | unknown — recover first |
| 9 | Legacy model-ID defaults (`text_model="gemini-3.8"` etc.) | Audit-guessed IDs ≠ API IDs | Already cured at runtime by Auto-pick/self-heal; keep roster-driven everywhere | done (keep) |
| 10 | v3 rescue unconfirmed | Boot migration written & synthetic-tested, never seen on real data | Ask him to check the green banner; keep `jarvis_academic.db` beside `butler.db` until confirmed | one question |

*Historical, already cured:* `no such table: system_config` after deleting the DB mid-run (N7 self-heal `get_conn`), `run_setup` 500 on omitted university (NOT NULL coalescing), tools.py `NameError` imports (N1), deadline parser regressions (B5/N-fixes), double-counted marks (N6 upsert), `turns.role` CHECK vs 'model' (live layer writes 'jarvis').

---

## 3. Timeline of everything done so far (reconstructed from all uploads)

| When (UTC) | Event |
|---|
| **15 Nov** | v3 "Butler" audited: working academic scheduler + voice front end; 12 real tasks in `jarvis_academic.db`; voice tool-call loop proven (episodic rows 47–50). Audit delivers B1–B12, R1–R9, G1–G8, A1–A5, protocols P0–P10, target architecture, M0–M7 queue, decisions D1–D7. |
| **16 Nov ~04:05–04:39** (chat A) | Fresh-instance assessment: project at M1→M2 boundary, ~40% of the 16-file queue. Whole stack booted in sandbox; 2 crashing tools + 5 more findings reproduced. **Marks policy locked** (4×3.75 + 4×2.5 + 25 + 50 = 100). **`registry.py` built (#10)** — 22 tools, all exit tests green first run; tools.py imports repaired; `/api/manifest` added; `.env.example` created. Decisions: audit order + staged Android (laptop → phone browser → APK). He installs, deletes DB → 500 (later cured by N7 self-heal). |
| **16 Nov (sessions between exports)** | M2 completed (#11 calculators + #12 academics end-to-end: real dashboard, feasibility chips, wake-prompt button, mark→prompt reactivity; `READ_ME_FIRST.txt` documents this zip). **M3 built** (#13–#16: LiveSession state machine, split text path, auth+relay+pins, Night Desk HUD). 197 checks green. Real-key incident: 404 on guessed model IDs → **models.py roster + Auto-pick + 404 self-heal hotfix** shipped (`jarvis_v4_model_fix.zip`). Typed path confirmed on his key; Live WS still 404s at handshake → session-2 context snapshot + `diagnose_live.py` cure plan. |
| **16 Nov 10:25–10:54** (chat B, "session 3") | Full 17-file intake, version-verified. **`FILE_INDEX.md` created** (line-numbered map; ~10× cheaper onboarding). New 4-door `diagnose_live.py` shipped. His run: SDK 404 / raw v1beta PASS 344 ms / raw v1alpha PASS 369 ms / REST 28 models → **root cause proven in SDK source**; **v1beta override applied** to session_manager.py + server.py probe; fix zip built (2 files + READ_ME); FILE_INDEX refreshed. ⛔ Usage limit hit before context update / delivery confirmation. |
| **16–18 Nov (session not in exports)** | **Kokoro voice build** created & installed on his laptop: mic → `/api/stt` (Gemini transcribes) → text brain → sentence-split → `/api/tts` (Kokoro-82M local, 14 voices incl. "Lewis · Classic British Butler"), edge-tts fallback installed; persona rewritten warm; 28 tools; flash-lite text preference; Aoede live-voice default. This branch never received the v1beta fix. |
| **18 Nov 06:18–07:19** (chat C) | Intake of the laptop build; **8 landmines found** (F1/F3/F4 headline). His full traceback + pip_freeze prove STT 500 = missing python-multipart; webm incompatibility confirmed via docs. App booted in sandbox rehearsal (28 tools, schema v1). **Kokoro benchmarks run** (cold 0.96 s; RTF 0.8–0.97; threads=1 best; maxph=40 → TTFC 1.1 s). Fixes begun (server.py ×3, database.py ×1, session_manager.py ×3 — last edit failed). ⛔ Usage limit hit. **Nothing shipped; work unlogged** — hence this consolidation. |
| **19 Nov (today)** | All 7 handoff files verified complete; this plan + extensive README produced. |

---

## 4. Decisions register (locked unless Haider changes them)

| ID | Decision | Value / rationale |
|---|---|---|
| Marks | Weighted points | 4 quizzes × 3.75 = 15 · 4 assignments × 2.5 = 10 · mid 25 · final 50 → 100/course (registry + `calculators.MARKS_POLICY`) |
| Build order | Audit queue, one milestone at a time | M0–M3 done; M4 next after voice is verified |
| **D1 Hosting** | **Oracle Cloud Always Free ARM** (delegated to agent, decided 16 Nov) | 2 OCPU / 12 GB Ubuntu 24.04, 200 GB disk, **10 TB/mo free egress** (voice ≈ 170 MB/h; GCP free ~1 GB egress would bill). June-2026 limits: 3,000 OCPU-h + 18,000 GB-h/mo → 24/7 use ≈ 1,460/8,760, safely inside. Fallbacks: ~$4 Ubuntu VPS (same script), GCP e2-micro (egress caveat). **Order: real voice on laptop FIRST, then cloud.** Guide: `DEPLOY.md` + `deploy/setup-oracle.sh` (Caddy auto-HTTPS via {ip}.sslip.io, systemd, token gen, nightly backup cron) |
| D2 Shell | Native Kotlin + Compose | echo cancel, foreground service, alarms, wake word are first-class |
| D3 Voice path | Direct-to-Gemini w/ ephemeral tokens, relay fallback | tokens Preview: 1 min start / 30 min send; locked to model+config; key never in APK |
| D4 Accountability | L2 academics, L1 elsewhere; quiet 23:00–07:00; nag budget 5/day | ladder L1 nudge → L2 ask why → L3 renegotiate → L4 log+review; Jarvis can never raise intensity |
| D5 Hands-free | Push-to-talk + notification action first; wake word at M7 | foreground service only during sessions; idle close |
| D6 Memory | Fresh build to audit §5 | provenance + freshness + supersede + vault implemented |
| D7 Database | SQLite single-user (`butler.db`) | Postgres only if multi-user ever happens |
| R8 Auth | `JARVIS_ACCESS_TOKEN` in `.env` | enforced on all `/api/*` + `/ws/*` (X-Jarvis-Token or ?token=; WS close 4401, origin 4403); unset = local dev + boot warning; **must be set before cloud exposure** |
| Android route | Staged | laptop voice → phone browser (cloud; Tailscale tunnel as stopgap) → Kotlin APK at M7 |
| Models (voice) | `gemini-3.8-live` primary | docs example ID; his dashboard shows it in use; spare tyres `gemini-3-flash-live`, `gemini-2.5-flash-native-audio-preview-12-2025`. Live quota effectively unlimited (65K TPM; his peak ≈ 6%) |
| Models (text) | Roster-driven; **flash-lite preferred** in current build (15 RPM / 500 RPD) vs 3.8 Flash (5 RPM / 20 RPD ≈ 6 tool-heavy questions/day) | exact IDs always read from Settings roster dropdown — never guessed |
| Key safety | Never in chat / code / APK | `.env` only; `.env.example` is the shareable template |
| Budget | Free-tier discipline | one consolidated test run per build; skeleton-first file reads; no wasteful searches |

**Open decisions to surface (not silently decide):**
- **OD1 — Canonical voice strategy:** keep the Kokoro STT→text→TTS pipeline as the everyday voice (free, local, works over plain HTTP) AND port the v1beta fix so Gemini Live becomes the premium "true voice" mode? *(Recommendation: yes — both, with a HUD switch.)*
- **OD2 — Text model for the test week:** flash-lite (500 RPD stamina) vs 3.8 Flash (sharpest tool judgement, 20 RPD). Current build prefers lite; confirm with him.
- **OD3 — Identity facts:** audit found hardcoded "BBA 6th Semester" vs his "5th" — one editable profile row must settle it (G3).
- **OD4 — Rescue confirmation:** did the green banner appear with his 12 v3 tasks? One question, gates deleting the old DB.

---

## 5. Architecture

### 5.1 Current laptop build (Branch B)

```
BROWSER — Night Desk HUD (hud.html)
  mic: getUserMedia EC/NS/AGC → AudioWorklet PCM16 16k ─┐  (Live mode)
  mic: MediaRecorder webm ──► POST /api/stt ────────────┤  (Kokoro mode)
  playback: 24k PCM queue (Live) / WAV per sentence (TTS)
  typed composer ──► WS /ws/live {type:text}            │
                                                        ▼
FASTAPI server.py  (auth gate R8 · dotenv · boot rescue · registry.register_all)
  ├─ /ws/live relay ──► session_manager.LiveSession
  │     REAL: Gemini Live (AUDIO, tools, resumption, compression, VAD)  ← needs v1beta port
  │     REHEARSAL: local understudy through the SAME registry/ledger/confirms
  ├─ typed path ──► text_stream.stream_reply (≤4 tool rounds, fallback chain,
  │     thought_signature preserved; never touches the audio session — R5)
  ├─ /api/stt ──► Gemini transcription (hardcoded model — F3; webm — blocker #2)
  ├─ /api/tts ──► Kokoro-82M ONNX local (14 voices; pre-warmed; lag — blocker #3)
  ├─ /api/models + autofix ──► models.py roster (28 models on his key)
  └─ CRUD: tasks/facts/assessments/commitments · tools/run+confirm · settings
        sessions/turns · backup · probe · undo (30 s ledger)
DATA — butler.db (SQLite, 65 tables per context record)
  memory: facts/rules/sessions/turns/summaries/state_signals (+FTS5)
  academics: courses/assessments/grading_scale/syllabus/study_sessions/spaced_reviews
  planning+accountability: tasks/time_blocks/plan_items/commitments/outcomes/reviews
  ops: audit_log (undo_json) / schema_migrations / capabilities / tool_specs
  assembler.py → token-budgeted wake packet (identity, grade truth, today,
  commitments, capabilities, state-age; facts w/ vault+proposed excluded; ~6 turns)
```

### 5.2 The Live-404 proof chain (keep for the merge)

1. His `diagnose_live.py`: SDK connect → `InvalidStatus HTTP 404`; raw `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=…` → `setupComplete` in 344 ms; v1alpha → 369 ms; REST models.list → 28 models.
2. SDK source (google-genai 3.3.0): `_api_client.py` L151 `GOOGLE_AI_API_DEFAULT_VERSION = 'v1'`; `live.py` L1044 `version = http_options.api_version`, L1069 builds the URI from it; `_merge_live_http_options` passes client options through untouched when no session options are given.
3. Therefore the 404 happens **before the model name is sent** — model swaps/Auto-pick could never fix it.
4. Fix (Branch A, port to B): `genai.Client(api_key=…, http_options=gt.HttpOptions(api_version="v1beta"))` in `session_manager._connect` (~L210–218) and the probe's live client in `server.py` (~L550). Alternative no-code cure: `pip install -U google-genai` (newer SDK targets v1beta) — then re-pin requirements to the *reported working* version, never a guess.

### 5.3 Target architecture (audit §4, unchanged)

Android thin shell (audio I/O, wake/PTT, notifications, 48 h offline nudge cache, Compose dashboard, direct Live via ephemeral token) ↔ always-on Python brain (REST + WS relay fallback, assembler, memory engine, planner, proactive scheduler, domain packs, tool runtime, SQLite, audit, evals) ↔ Gemini (Live voice+tools; generate_content for text/summaries/extraction).

---

## 6. Phase 0 — Make the whole voice loop work (next work session, ~1 sitting)

**Goal:** on his laptop, one build where typed ✅, mic→STT ✅, sentence-streamed local TTS ✅ (< ~1.5 s to first audio), Gemini Live ✅ (v1beta), all 28 tools + confirms + undo intact.

### Step 0 — Re-assemble the workspace (his §0 rule: ask FIRST, never reconstruct)
Request from him (`.txt` copies fine): **Branch B laptop files** — `server.py`, `session_manager.py`, `text_stream.py`, `models.py`, `registry.py`, `tools.py`, `database.py`, `calculators.py`, `assembler.py`, `static/hud.html`, `static/setup.html`, `static/dashboard.html`, `requirements.txt`, `.env.example`, plus `pip_freeze.txt`. Also ask for **`INTAKE_SESSION3.md`** (holds F2/F5–F8), **`FILE_INDEX.md`**, the session-3 **`JARVIS_CONTEXT.md`** (28,256 B version), `DEPLOY.md`, `deploy/setup-oracle.sh`, and the 16 Nov fix zip if he still has it. *(None of these are needed for today's plan/README — only for the code session.)*

### Step 1 — The one-command unblock (he can do this before any build)
```bash
cd ~/Downloads/Butler3.0 && source venv/bin/activate
pip install python-multipart
```
Restart uvicorn → mic should get past the 500. Expect the *next* error to be webm-related (blocker #2) — that's progress, not regression.

### Step 2 — The consolidated voice-fix build (Branch B base + Branch A port)
One zip, exact replace-list, one consolidated test run:
1. `/api/stt`: accept multipart robustly (keep python-multipart in requirements; optional dependency-free fallback), **convert webm→WAV server-side**, and pick the STT model from the roster / a `stt_model` config row (kills F3).
2. `/api/tts`: **threads=1**, phoneme batching **maxph≈40**, sentence/clause streaming via Kokoro `create_stream`, keep pre-warm; HUD TTS queue already streams per sentence — verify interplay (target: first audio ≈ 1.1 s warm).
3. **F4:** Browser-voice toggle stays visible and truthful in real mode; local TTS never silently gated.
4. **Port the v1beta override** into `session_manager._connect` + probe client (2 surgical edits, §5.2). Keep the 404 self-heal as belt-and-braces.
5. `requirements.txt`: add `python-multipart`, `kokoro-onnx==0.6.1`, `soundfile==0.14.0`, `edge-tts==7.3.1` (+ transitive pins from his pip_freeze: onnxruntime, numpy, phonemizer, espeakng-loader). Re-pin `google-genai` only if he upgrades and reports the working version.
6. Recover F2/F5–F8 from `INTAKE_SESSION3.md` (or re-audit) and fold any remaining fixes into the same build.
7. Update `JARVIS_CONTEXT.md` + `FILE_INDEX.md` **before** the session ends (the 18 Nov lesson: ship docs with the zip, not after).

### Step 3 — Laptop verification script (single consolidated run)
1. Boot → banner REAL mode; **green rescue banner?** (OD4 — report yes/no).
2. Type "what do I need on the final for AMA?" → truthful calculator verdict, first token < 1 s.
3. Hold brass button, speak → transcript appears (STT), Jarvis answers aloud (TTS) with ≤ ~1.5 s to first audio.
4. "add task …" then "delete task …" → approval card → yes → gone → 30 s undo works.
5. Type mid-speech → must not cut him off (R5).
6. System → Run probe → all green incl. Model roster + Live handshake; paste results (no key).
7. Switch to Gemini Live mode (post-port) → hold and talk → real voice; watch breadcrumb.

### Step 4 — M3 exit test (the official one, still owed)
**30-minute session surviving two reconnects (GOAWAY→RESUMING), no lost turns, text first token < 1 s, voice start < 3 s.** Run on the Live path once Step 3.7 passes; the Kokoro path gets an equivalent endurance check (it rides plain HTTP, so reconnect semantics differ — document both).

**Exit criteria for Phase 0:** all of Step 3 green on his machine + Step 4 reported + context/index updated + rescue banner answered.

---

## 7. Phase 1 — Cloud: Jarvis without the laptop (after Phase 0)

1. Provision **Oracle Always Free ARM** (2 OCPU / 12 GB, Ubuntu 24.04) — D1. Capacity can be scarce in popular regions; retry or fall back to a ~$4 VPS (same script).
2. Run `deploy/setup-oracle.sh`: Caddy auto-HTTPS ({ip}.sslip.io), systemd service, **`JARVIS_ACCESS_TOKEN` generated and enforced**, nightly backup cron. Verify free-tier limits at build time (they change).
3. Copy the project + `butler.db` (or let boot rescue run); `pip install -r requirements.txt` (now complete — no deaf/mute box); Kokoro model files (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) must be provisioned too.
4. Smoke: phone browser → HTTPS HUD → lock screen token → typed + voice round-trip; probe green; backup downloads.
5. Cost guard stays: `live_minute_cap` 90 (raisable — his Live quota is effectively unlimited), idle close 120 s.
**Done when:** Jarvis answers from his phone with the laptop closed, TLS + token on, nightly backup proven by a restore drill.

## 8. Phases 2–5 — the remaining audit queue

| Phase | Milestone | Build | Exit test |
|---|---|---|---|
| 2 | **M4 Planner + proactive** | `planner/*` constraint scoring (duration, deadline slack, energy, location, dependencies, load cap) replacing QP-at-risk; `proactive/*` triggers (pre-block, block-end outcome ask, T-24h/T-3h, lecture debrief, stale state/fact, missed-block replan, reviews, patterns), channels, quiet hours, ladder L1–L4 | **A missed block triggers a replan offer unprompted** (no socket open); 20 scripted scheduling cases pass |
| 3 | **M5 Learning** | capture→aggregate→propose→apply→monitor→rollback; adherence, estimation error, velocity per course/method, energy by hour; weekly review digest | A learned parameter changes a plan **and can be rolled back**; weekly report shows adherence + estimation error |
| 4 | **M6 Evals & ops** | golden-dialogue evals (slotting, stale-state re-ask, interrupted-turn resume, gates), migration/backup/restore drills, structured logs | **Evals run after every change and block regressions** |
| 5 | **M7 Android** | Kotlin+Compose thin shell; PTT + notification action; `/session/start` → ephemeral token (Preview: 1 min/30 min) locked to model+config → direct Live; tool calls → `/tools/execute`; resumption handle on GoAway; relay fallback; 48 h offline nudge cache + queued check-ins; foreground service only during sessions; daily Live-minutes cap shown | **Same conversation, same database, phone and laptop** |

Throughout: P10 privacy/safety stays on (sensitivity tiers, confirms, audit, kill switch, off-record, vault, injection hygiene R7).

---

## 9. Standing protocols (owner + agent rules)

**Owner profile:** not a programmer, self-described forgetful → plain English, gentle reminders, **exact download/replace file lists every delivery**; charismatic butler energy, motivational uplift, no stress, he does not judge; free-tier → one consolidated test run per build, minimal searches, skeleton-first reads.

**Agent protocol (his explicit rules):**
1. Before writing ANY code, verify the workspace has current copies of the essential files; if missing/stale, **ask him to attach them FIRST** — never reconstruct from memory.
2. One milestone at a time in audit order; disclose out-of-file repairs; surface decisions (OD1–OD4 style) instead of guessing.
3. **Never ask for his API key in chat.** Key only in `.env`; never in code/chat/APK. Upload `.env.example`, never `.env`.
4. Keep `JARVIS_CONTEXT.md` updated **every session** (and `FILE_INDEX.md` after edits — line numbers shift). The 16 Nov and 18 Nov sessions both died at the usage limit *before* docs were saved; **write docs early, ship them with the zip.**
5. Budget protocol: onboard via context + index (~40 KB) instead of full reads (~360 KB); jump to exact line ranges; refresh the index with one command after edits.
6. Sandbox realities: venvs/processes outside the workspace don't persist between instances; system python may lack pip (bootstrap via get-pip.py); node v20 present; rebuild before testing. **Real Gemini audio has never run from a sandbox (no key) — never claim it verified.**
7. Sandbox-only scripts (`exit_tests*.py`, `seed_demo.py`, `hud_smoke.js`) never ship to his laptop — they wipe/fake databases on purpose.
8. Uploads arrive as `.txt` copies — rename to real filenames; verify version markers (e.g., Auto-pick button, v1beta string, tool count) before trusting them.

---

## 10. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Usage limit kills a session mid-build (happened 3×) | High | Unlogged work, divergent branches | Docs-first discipline; small shippable increments; this plan + README as the recovery baseline |
| Branch drift (A vs B vs future) | Already happened | Regressions on install | Version-marker checks; single canonical base (B) + surgical ports; exact replace-lists; FILE_INDEX refresh |
| Guessed model IDs recur (original 404, F3) | Medium | Silent breakage | Roster-driven everywhere incl. STT; Auto-pick; never hardcode suffixes |
| Oracle free capacity unavailable | Medium | Cloud delayed | ~$4 VPS fallback runs the same script; GCP e2-micro with egress caveat |
| Free-tier text quota (20 RPD on 3.8 Flash) | Medium | Mid-test wall | flash-lite preference (500 RPD); dropdown flip documented |
| Kokoro CPU latency on small cloud ARM | Medium | Sluggish voice in cloud | threads=1 + maxph≈40 + streaming already benchmarked; edge-tts fallback installed; Live mode as alternative |
| Real-data rescue never confirmed | Medium | 12 tasks stranded | OD4 question + keep `jarvis_academic.db` until banner seen; backups before any migration |
| Key exposure | Low | Critical | .env-only rule; token auth before cloud; ephemeral tokens for APK; never upload real .env |
| SQLite single-user limits | Low (by design) | Future multi-user | D7 revisited only if needed; Postgres path documented in audit |
| Identity drift (semester 5 vs 6, targets) | Medium | Wrong advice | G3 profile rows; OD3 |

---

## 11. File inventory & handoff map

**Received & verified today (all non-empty, all parseable):**

| File | Size | Content |
|---|---|---|
| `uploads/AUDIT.txt` | 25,017 B | Full audit, 15 Nov (findings, protocols, architecture, queue, decisions) |
| `uploads/Jarvis v4 — Audit, Improvement Protocols & Framework.txt` | 25,017 B | **Byte-identical duplicate of AUDIT.txt** (same MD5) — keep one canonical copy |
| `uploads/JARVIS_CONTEXT.txt` | 21,591 B | Authoritative context, 16 Nov session-2 snapshot (§0–§10 incl. Live-404 incident) — **predates session-3 fix and the Kokoro build** |
| `uploads/READ_ME_FIRST.txt` | 2,175 B | Item-#12 (M2 complete) zip readme: dashboard/wake-prompt features, install list, sandbox-script warning |
| `uploads/chat-export-1794804134044.json` | 697,042 B | Chat A, 16 Nov 04:05–04:39: assessment + registry build + marks decision (8 messages) |
| `uploads/chat-export-1794826550346.json` | 964,814 B | Chat B, 16 Nov 10:25–10:54: intake, FILE_INDEX, diagnose verdict, **v1beta fix applied** (6 messages; ends at usage limit) |
| `uploads/chat-export_Previous working which is not logged in context or readme.json` | 913,228 B | Chat C, 18 Nov 06:18–07:19: Kokoro-build intake, 8 landmines, STT root cause, benchmarks, unfinished fixes (4 messages + one empty "New chat" stub; ends at usage limit) |

**Referenced in the record but NOT uploaded (request before the next code session):** Branch B code files (list in §6 Step 0), `INTAKE_SESSION3.md` (F2/F5–F8), `FILE_INDEX.md`, session-3 `JARVIS_CONTEXT.md` (28,256 B), `diagnose_live.py`, `DEPLOY.md`, `deploy/setup-oracle.sh`, `jarvis_v4_M3.zip`, `jarvis_v4_model_fix.zip`, the 16 Nov live-fix zip (session_manager + server + READ_ME), `butler.db` / `jarvis_academic.db` (or a text export of the last ~100 audit/turn rows — direct DB upload hit a parsing error; JSON export also failed; a terminal snippet is the workaround).

**Produced today:** `PROJECT_PLAN.md` (this file) · `README.md` (extensive project readme) · `extracted/*.txt` (readable transcripts of the three chats, for reference).

---

## 12. Definition of done & metrics

- **Phase 0:** mic→STT→brain→TTS round-trip on his laptop; first audio ≤ ~1.5 s warm; Live mode connects via v1beta; probe all-green; 28 tools + confirm + undo intact; rescue banner answered; docs updated.
- **M3 official exit:** 30 min, two reconnects, zero lost turns, text first token < 1 s, voice start < 3 s.
- **Phase 1:** phone browser works laptop-off; TLS + token enforced; nightly backup + one tested restore.
- **M4:** unprompted replan offer on a missed block; 20 scheduling cases pass.
- **M5:** a learned parameter visibly changes a plan and rolls back cleanly.
- **M6:** evals gate every change.
- **M7:** one conversation, one database, phone and laptop.
- **Always:** every write audited & reversible; destructive needs yes; vault never leaked; key never leaves `.env`; weekly review shows adherence + estimation error (once M5 lands).

---

## 13. Next-session kickoff checklist

1. Attach this `PROJECT_PLAN.md` + `README.md` (and `JARVIS_CONTEXT.md` once refreshed).
2. Agent reads §2.2 (two branches) and §6 (Phase 0) first; asks for the §6 Step-0 files **before any code**.
3. Confirm OD1–OD4 with Haider (four short questions).
4. Execute Phase 0 as one consolidated build + one test run; update context + index; ship zip with exact replace-list.
5. Then, and only then, Phase 1 (cloud). 🎩

*The butler's voice will be heard. Truth before voice; the LLM proposes, code disposes.*
