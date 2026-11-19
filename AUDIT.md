# Jarvis v4 — Audit, Improvement Protocols & Framework

Prepared 15 Nov 2026 for Haider. Scope: `database.py`, `tools.py`, `server.py`, `index.html`, `requirements.txt`, `jarvis_academic.db`, `project_details.txt`.

## 0. How this was checked

I read every file, opened a copy of the database, reproduced the suspected bugs against a scratch copy of your code, and checked Live API behaviour against Google's current docs. Tags used below: **\[Reproduced\]** I ran it and saw it fail. **\[DB\]** seen in your database. **\[Docs\]** stated in Google's Live API docs. **\[Verify\]** needs a check on your key and SDK version before we depend on it.

## 1. Verdict

What exists is a working academic scheduler with a voice front end. It is not yet a life butler: it has no durable memory, no way to act unprompted, and its domain knowledge lives in code instead of data. It also has real bugs, two of which silently lose or hide your data.

What holds up and stays: the database / tools / server separation, state injection on wake, PKT enforcement, the send locks, and the tool-first loop (on 15 Nov you asked by voice, `log_syllabus_and_schedule` slotted it, and Jarvis confirmed day, window and location: `episodic_memory` rows 47-50). The model IDs in the log are valid per the docs.

## 2. Audit findings

### 2A. Bugs and data integrity

| ID | Finding | Evidence | Severity |
| --- | --- | --- | --- |
| B1 | `log_syllabus_and_schedule` inserts into `syllabus_progress` on a connection that is closed without `commit()`. The row is rolled back every time. | \[Reproduced\] row count stays 0 after a call. \[DB\] 0 syllabus rows although the tool ran on 15 Nov at 11:54. The Syllabus Coverage Tracker can never fill. | Critical |
| B2 | The prompt's task list uses `ORDER BY assigned_date ASC LIMIT 10`. SQLite sorts NULL first, so nine legacy unslotted tasks crowd out real ones. | \[Reproduced\] prompt holds tasks 1-10. The two you confirmed last session (task 12 AMA Ch6 Mon 13:00, task 11 RTT Ch6 Tue 21:00) are omitted. | High |
| B3 | `is_surprise_quiz = 1 if deadline_hint else 0` makes every deadline a surprise quiz, which then sorts first everywhere. | \[Reproduced\] new test task flagged. \[DB\] task 10 (GBM presentation) is flagged. | Medium |
| B4 | The migration adds slot columns but never backfills, and nothing detects duplicates. | \[DB\] 9 of 12 pending tasks have no slot. GBM Ch13 presentation exists twice (tasks 9 and 10). | Medium |
| B5 | Deadlines are stored as words ('Wednesday') and re-resolved against today. The parser returns None for 'today', 'Nov 18', 'next week'. 'tomorrow 9am' becomes 23:59. A weekday equal to today jumps 7 days. A None deadline silently removes the deadline filter. | \[Reproduced\] | Medium |
| B6 | Spec-code drift. Gate 2 (stress >= 4 blocks RTT) is not implemented. The T-14 lockdown only labels; nothing suspends the sandbox. 'Auto-alert at 11:15' has no mechanism. The spec says 17 Gemini tools; 15 are declared, so `log_lecture_debrief` and `set_voice_name` cannot be called by voice. | \[Reproduced\] stress=5 still returns READY\_TO\_EXECUTE. | High (trust) |
| B7 | `trigger_missed_block_rollover` hardcodes 45 min x 3 days instead of reading `system_config`, moves no task, and only bumps `rollover_count` on three tasks. | \[Reproduced\] 200 missed minutes reports 65 min overflow; nothing is scheduled. | Medium |
| B8 | `upsert_schedule_slot`: update drops `secondary_course`; insert silently defaults `primary_course` to AMA. | Code | Low-Medium |
| B9 | Two timestamp formats plus SQLite `CURRENT_TIMESTAMP` (UTC). No ISO offsets. | \[DB\] check-ins 1-3 are UTC-style, check-in 4 is '... PKT'. | Medium |
| B10 | Dead schema: `lecture_logs` and `sandbox_logs` have no writer; `checkins.resulting_focus` is never written; spaced-repetition rows are created on every session (duplicates) and never surfaced. | \[DB\] 0 rows in `sandbox_logs`, `spaced_repetition`, `study_sessions`. | Low-Medium |
| B11 | `find_best_slot_for_task` ignores task length vs slot length, caps nothing (load is -1 point per 15 min), lets a slot straddle the deadline, ignores energy, gates and location, and invents 'tomorrow 21:00' if nothing fits. | Code | Medium |
| B12 | No foreign keys, indexes, CHECK constraints or schema versioning. Connections are not closed on exceptions. | Code | Low |

### 2B. Real-time layer

| ID | Finding | Evidence | Severity |
| --- | --- | --- | --- |
| R1 | No `session_resumption`, no `context_window_compression`, no GoAway handling. Without compression an audio-only session is capped at 15 minutes, one connection lasts about 10 minutes, a GoAway message warns before it ends, and resumption handles stay valid for 2 hours. The dev log's 'transient inactivity' diagnosis is probably incomplete. | \[Docs\]. \[DB\] rows 39-46: two replies never logged and you had to say 'you did not complete your last response' twice. To confirm: log seconds-since-connect on every close; clustering near 10 or 15 minutes means lifecycle. | High |
| R2 | Three more silent kill paths. (a) A bare `except Exception: pass` in `client_to_gemini` ends that task, and `FIRST_COMPLETED` then cancels the other. (b) Tool exceptions and unexpected model arguments (`fn(**args)`) are uncaught inside `gemini_to_client`. (c) `send_client_content(turn_complete=True)` interrupts generation, so typing or a dashboard `quick_tool` mid-sentence cuts Jarvis off. | Code + \[Docs\] | High |
| R3 | Synchronous SQLite and tool work runs inside the async receive loop, stalling audio both ways while a tool runs. The docs support NON\_BLOCKING function calls with a scheduling mode (INTERRUPT / WHEN\_IDLE / SILENT); unused. | Code + \[Docs\] | High (latency) |
| R4 | Jarvis text is saved only on turn\_complete or interrupted; user text only once the reply starts; neither on exceptions. The docs note that with async reasoning turnComplete does not mean idle; an interaction status field must be used. | Code + \[Docs\] + \[Verify\] field names in the pinned SDK | Medium |
| R5 | Typed text goes through the audio session, so text waits on speech synthesis (your log item 3). Agreed. | Code | Medium |
| R6 | Web client: deprecated ScriptProcessorNode on the main thread, a fixed RMS > 12 auto-wake threshold, and a speaker/headphone toggle that works around missing echo handling. | Code | Low-Medium |
| R7 | `innerHTML` renders model-written strings (titles, topics, notes) without escaping. | Code | Medium |
| R8 | No auth on `/ws/live`, `/api/state`, or `quick_tool` (which can call every delete tool). Acceptable on 127.0.0.1 only. | Code | Blocker for phone |
| R9 | `requirements.txt` uses open `>=` bounds; the Live API surface is version-sensitive. | File | Low |

### 2C. Gaps against what you asked for

| ID | Gap | Evidence |
| --- | --- | --- |
| G1 | Memory is the last 40 rows globally, 12 injected. No sessions, summaries or facts. Anything older than about 20 exchanges is gone. | Code, \[DB\] |
| G2 | Nothing is learned. Check-ins are never analysed (4 rows total), `learned_velocity` is an EMA over arbitrary units, and nothing records whether a planned block happened. | Code, \[DB\] |
| G3 | Identity and strategy are hardcoded in the prompt: targets ('48/50 + 2.5 Qz -> 71%'), instructors, 'BBA 6th Semester'. Your message says 5th. Either may be right, which is the point: identity facts need one editable source. Updating marks leaves the prompt stale. | `server.py` |
| G4 | The schema is academics-only. No projects, work, money, health, habits, goals, people or journal. | `database.py` |
| G5 | No proactivity: Jarvis exists only while a socket is open. `working_state` is labelled authoritative with no age; its row is from 11:35 while the last log entry is 18:04. | Code, \[DB\] |
| G6 | `qp_at_risk` = credits x (GP at target - GP at target minus 6 marks). That is a fixed 6-mark sensitivity, not risk, and it ignores feasibility. AMA is 20/50 before the final: a perfect 50/50 final gives 70, which is B- (B needs 71), so a B is unreachable without quiz salvage. Yet AMA (1.98) ranks below RTT (2.01) with no feasibility flag. Your pushback in rows 68-72 was correct, and Jarvis's explanation (row 67) was improvised because the formula never reaches the model. | \[DB\], `database.py` |
| G7 | No self-model. Jarvis has no manifest of its tools, metric definitions or limits (row 75: changes 'would need my development team'). 'Learns and adapts' needs data-defined extension, not code edits. | \[DB\] |
| G8 | No accountability ledger: commitments, outcomes, reasons, adherence. | Schema |

### 2D. Android blockers

- **A1** A FastAPI + HTML app cannot simply be wrapped into an APK. A WebView still needs the server running somewhere, and a sleeping laptop is not anywhere, anytime.
- **A2** The API key must never ship inside the APK.
- **A3** Browser audio assumptions do not carry over. Android has a hardware-backed echo-cancelling voice source, which retires the speaker/headphone toggle. \[Verify\]
- **A4** Hands-free background listening needs a foreground service with a visible notification, and battery management (Doze, vendor task killers) will interfere. \[Verify on your Android version\]
- **A5** A Live session held open all day costs money, battery and privacy. Start sessions by wake word, push-to-talk or a notification action, and close them after idle.

## 3. Improvement protocols

| P | Protocol | What changes | Exit test |
| --- | --- | --- | --- |
| P0 | Stabilise | Fix B1-B12, R1-R4, R9. Session resumption, compression, GoAway handoff, non-blocking tools, structured logs, pinned dependencies. | A 30-minute voice session survives two reconnects with no lost turn and no silent exit. |
| P1 | Data, not code | Identity, targets, grading scales, course strategy and prompt fragments move into tables. The prompt is assembled from data. | Changing a mark or target changes the next prompt with no code edit. |
| P2 | Memory | Layered memory with provenance, importance and freshness (section 5). | A question about something from three weeks ago is answered correctly; a stale fact triggers a re-ask. |
| P3 | Life domains | Domain-agnostic core plus domain packs. Academics becomes pack one. | A Fitness pack is added by manifest with no core edit. |
| P4 | Planner v2 | Constraint scoring on duration, deadline, energy, location and dependency; replan on miss; code does the maths, the LLM handles intent and narration. | 20 scripted scheduling cases pass. |
| P5 | Proactive engine | Scheduler, triggers, channels, quiet hours (section 7.3). | Jarvis reaches you with no socket open. |
| P6 | Accountability and learning | Commitments ledger, outcomes, weekly review, learned parameters with confidence and decay, all reversible. | Weekly report shows adherence and estimation error; a learned parameter changes the planner and can be rolled back. |
| P7 | Live layer rewrite | Session state machine, text path split from voice, capability manifest, barge-in. | Text first token under 1 s; voice start within your 3 s budget. |
| P8 | Android port | Thin native shell, cloud brain (section 8). | Same conversation works on phone and laptop against one database. |
| P9 | Observability and evals | Logs, golden-dialogue evals, DB migrations, backups, tests for the grade engine and planner. | Evals run after every change and block regressions. |
| P10 | Privacy and safety | Sensitivity tiers, confirmation on destructive tools, audit log, kill switch, injection hygiene. | Destructive tool calls need confirmation; every write is in the audit log. |

## 4. Target architecture

```
ANDROID SHELL (Kotlin, thin)        BRAIN (Python, always-on)               GEMINI
 audio I/O, echo cancel, wake/PTT    REST API + WebSocket relay (fallback)   Live API: voice + tools
 notifications, local alarms         Context assembler                       generate_content: text,
 48h nudge cache (offline)           Memory engine + freshness                 summaries, extraction
 Compose dashboard                   Planner, proactive scheduler
 direct Live session (ephemeral      Domain packs, tool runtime
   token)                            SQLite/Postgres, audit log, evals
```

Four rules the whole design obeys:

1. **The LLM proposes, code disposes.** Arithmetic, dates, scheduling and grade maths run in tested code; the model decides intent and speaks.
2. **Data, not code.** Anything you might want to change by talking (targets, rules, routines, metrics) is a row, not a constant.
3. **Every memory has provenance and freshness.** Who said it, when it was last confirmed, when to ask again.
4. **You approve what changes Jarvis.** Learned parameters, new tools and rule changes arrive as proposals with evidence; irreversible actions need confirmation.

## 5. Memory and freshness engine

| Layer | Holds | Lifetime | Tables |
| --- | --- | --- | --- |
| Working | Current session context | Session | RAM |
| Episodic | Sessions, turns, summaries | Raw 30 days, summaries kept | `sessions`, `turns`, `session_summaries` |
| Semantic | Facts about you: profile, preferences, people, goals | Until superseded | `facts` |
| Procedural | Your rules and protocols (course protocols, accountability ladder) | Versioned | `rules` |
| State | Fast signals: energy, location, fed, stress | Minutes to hours (TTL) | `state_signals` |

**Fact record:** id, domain, key, value, source (stated / observed / inferred), confidence, importance 1-5, created\_at, last\_verified\_at, verify\_after\_days, supersedes, sensitivity (normal / private / vault), status (active / proposed / retired).

**Write path.** The model saves memory through a `remember()` tool with an evidence snippet. Stated or confirmed facts go active; inferred ones go in as proposed and are confirmed by voice. Nothing is overwritten: a new value supersedes the old and keeps history. Vault-tier facts are never placed in the prompt unless the turn needs them.

**Freshness.** Each fact class has a default review interval, all editable: location hours; energy and hunger about 3 hours; weekly schedule 30 days; goals 30 days; body metrics 14 days; grades after each assessment; relationships 90 days; core identity 180 days. A daily job ranks facts past due by importance x staleness. The proactive engine asks at most N per day, at moments you are not in deep work. An answer resets `last_verified_at`.

**Read path (context assembler).** Fixed token budget. Always: identity core, today's plan, open commitments, the capability manifest. Then the top-k facts by relevance x importance x recency, the last session summary, and about six raw turns. Retrieval starts with SQLite FTS5; embeddings only if that proves insufficient.

**Consolidation (nightly).** Summarise sessions, extract candidate facts as proposed, dedupe, decay, and prepare the weekly digest.

## 6. Data model

- **Identity and memory:** `profile_core`, `facts`, `rules`, `sessions`, `turns`, `session_summaries`, `state_signals`.
- **Planning:** `domains`, `goals`, `projects`, `tasks` (domain-agnostic: domain, project, goal, estimate, deadline, energy need, location need, depends\_on, status), `time_blocks` (weekly template plus one-offs), `calendar_events`, `plan_items` (task to block, versioned).
- **Accountability:** `commitments`, `outcomes`, `reviews`, `learned_params`.
- **Proactive:** `triggers`, `nudges` (sent / seen / acted / snoozed), `quiet_hours`, `channels`.
- **Extension:** `tool_specs` (declarative), `proposals`, `capabilities`.
- **Operations:** `audit_log`, `eval_runs`, `schema_migrations`.
- **Academics pack:** `courses`, `assessments` (rows of type, weight, max, obtained; replaces the fixed quiz / assignment / mid columns), `grading_scales` (rows), `syllabus_items`, `study_sessions`, `spaced_reviews`.
- **Starter packs:** fitness, projects/work, finance, journal.

Time rule: store UTC ISO-8601 plus the zone name; render in Asia/Karachi.

## 7. Core contracts

**7.1 Declarative tool spec.** Fields: name, description, JSON-schema parameters, handler (Python path or SQL template), risk (read / write / destructive), confirmation policy, behaviour (BLOCKING / NON\_BLOCKING) and default scheduling. Gemini declarations are generated from specs at connect. Destructive tools require confirmation. SQL-template tools can be added through proposals, with your approval.

**7.2 Planner and metrics.** `plan(tasks, blocks, state, now) -> PlanDiff` with an `explain()` for narration. Score terms: course fit, deadline slack, task length vs block length, energy need vs predicted energy, location, dependencies, load cap. Replan triggers: a missed block, a new deadline, a gate firing. Academics calculators: required final for each grade threshold, feasibility, and expected GPA gain per study hour. These replace QP-at-risk as the ranking signal; QP sensitivity stays as a secondary column with its formula exposed to the model.

**7.3 Proactive triggers.**

| Trigger | Condition | Action |
| --- | --- | --- |
| Pre-block | 15 min before a block | Nudge with the block's ticket |
| Block end | A planned block ends | Ask outcome: done, partial or missed, plus a reason |
| Deadline | T-24h and T-3h | Nudge, offer replan |
| Lecture end | A lecture slot ends | Ask for the debrief |
| Stale state | State older than its TTL | Ask one question |
| Stale fact | Past review interval | Ask, within the daily cap |
| Missed block | Outcome is missed | Offer a replan within the configured rollover cap |
| Review | Evening, and the weekly audit | Run the review |
| Pattern | A learned pattern fires | Suggest, never impose |

**Accountability ladder (your policy, editable):** L1 nudge, L2 ask why, L3 renegotiate the plan, L4 log it and surface it in the weekly review. Intensity is set by you per domain. Jarvis cannot add levels or raise intensity on its own. Snooze and pause are always available.

**7.4 Learning loop.** Capture (outcomes, check-ins, session logs), aggregate (adherence, estimation error, velocity per course per method, energy by hour), propose (a change to a learned parameter with its evidence), apply (with confidence and decay), monitor, roll back if it worsens. Every change shows up in the weekly review.

**7.5 Live session state machine.** IDLE -> CONNECTING -> LIVE -> GOAWAY -> RESUMING -> LIVE -> CLOSING. A turn ledger commits each turn exactly once, with an interrupted marker if the turn did not finish and a rule that Jarvis continues from the cut instead of restarting. Typed text uses a separate streaming text path and never interrupts speech.

## 8. Android topology

**Verified in the docs:** `gemini-3.8-live` and an extended-thinking variant exist. Session limits, resumption and compression are as in R1. Function calls can be NON\_BLOCKING with scheduling. Ephemeral tokens exist for client-side use but are in Preview: by default one minute to start a session and 30 minutes to send messages, they can be locked to a model and config (so the system prompt can stay server-side), and the connection still needs resumption roughly every 10 minutes. Per-model feature differences in the docs table are easy to misread, so \[Verify\] them with a capability probe before relying on them.

**Recommended topology.** Brain on a small always-on cloud instance, native Android shell, voice direct to Gemini.

1. The app authenticates, then calls `/session/start`.
2. The server builds the prompt, mints an ephemeral token locked to model and config, and returns both.
3. The app opens the Live session directly, which avoids relaying audio through your server.
4. Tool calls are forwarded by the app to `/tools/execute` as NON\_BLOCKING; the result goes back as the tool response.
5. The app stores the resumption handle and reconnects on GoAway.
6. A relay mode on the server stays as fallback and powers the laptop HUD.

**Offline.** The server pushes a 48-hour nudge plan; the phone schedules local alarms from it, queues check-ins while offline, and syncs later.

**Phase 1 shortcut (single user, not distributed).** Enter the key on the device and keep it in Keystore-backed storage, never in source or the APK. Move to ephemeral tokens in Phase 2.

**Hosting.** One small instance with TLS and token auth, nightly encrypted backup. Free-tier limits and prices change, so \[Verify\] at build time.

**Client stack.** Native Kotlin with Compose for the shell, since echo cancellation, foreground service, alarms and wake word are first-class there. The intelligence stays in Python.

**Hands-free modes.** Wake word (on-device), push-to-talk, or a notification action. A foreground service runs only while a session is active, closing after idle. A daily Live-minutes cap is shown in the app.

## 9. Repo layout and build queue

```
jarvis/
  brain/  app.py  settings.py
    core/      db.py  clock.py  audit.py  migrations/
    memory/    facts.py  episodic.py  state.py  freshness.py  assembler.py  consolidate.py
    planner/   model.py  scoring.py  solve.py  metrics.py
    proactive/ triggers.py  scheduler.py  channels.py  ladder.py
    learning/  aggregates.py  params.py  proposals.py
    live/      session_manager.py  relay.py  tokens.py  text_stream.py  manifest.py
    tools/     registry.py  specs/  handlers/
    packs/     academics/ (manifest, schema.sql, calculators.py, prompts.md, checkins)
               fitness/  projects/  finance/
  web/       HUD v4
  android/   Kotlin shell
  evals/     scenarios/  runner.py
  tests/
  docs/      DECISIONS.md  PROJECT_REGISTER.md
```

| Milestone | Files, in order | Done when |
| --- | --- | --- |
| M0 Hotfix v3 | 1. patch for B1-B3, B5-B8, R2, R3 plus resumption, compression, GoAway. 2. `migrate_v3_to_v4.py` (backup, backfill, dedupe, timestamp normalisation) | Today's session survives reconnects; tasks 11 and 12 reach the prompt; syllabus rows persist |
| M1 Core and memory | 3. `core/db.py` + migration 0001. 4. `core/clock.py`. 5. `memory/facts.py` + `freshness.py`. 6. `memory/episodic.py`. 7. `memory/state.py`. 8. `memory/assembler.py`. 9. `live/manifest.py` | A fact asked about three weeks later is recalled; a stale fact is queued for re-ask |
| M2 Tools and academics | 10. `tools/registry.py` + spec schema. 11. academics pack (schema, calculators with required-final and feasibility). 12. academics handlers | Dashboard shows feasibility; changing a mark changes the prompt |
| M3 Live layer | 13. `session_manager.py`. 14. `text_stream.py`. 15. `app.py` + `relay.py`. 16. HUD v4 with session history | 30-minute session, no lost turns, text first token under 1 s |
| M4 Planner and proactive | `planner/*`, `proactive/*`, commitments | A missed block triggers a replan offer unprompted |
| M5 Learning | `learning/*`, weekly review, proposals | A learned parameter changes a plan and rolls back |
| M6 Evals and ops | `evals/*`, backups, logs | Evals gate every change |
| M7 Android | Kotlin shell, `tokens.py`, offline cache | Same database, phone and laptop |

## 10. Additions beyond your brief

- **Capability probe script.** Confirms model IDs, tool scheduling, resumption, ephemeral tokens and Urdu / Roman-Urdu input on your key before we depend on them.
- **Undo and audit log.** Every write is reversible and attributable.
- **Kill switch and privacy modes.** Mic off, off-the-record turns that are never stored, a vault tier excluded from prompts.
- **Nag budget.** Quiet hours, daily nudge caps and snooze, so accountability does not turn into noise.
- **Cost guard.** Daily Live-minute cap and usage log.
- **Eval harness.** Golden dialogues covering slotting, stale-state re-ask, interrupted-turn resume and gate behaviour; this is the regression net for P0-P7.
- **Backups and a restore drill.** Nightly snapshot, tested restore.
- **Names and codes.** Course codes and personal names injected as context hints, since name recognition is a known weak point in voice.
- **One repo, one register.** `PROJECT_REGISTER.md` and `DECISIONS.md` so there is a single authoritative copy.
- **Memory reuse.** If the memory engine from your other Jarvis build is the one you trust, upload it and I will fit it into section 5 instead of redesigning it.

## 11. Decisions that are yours

| ID | Decision | My recommendation |
| --- | --- | --- |
| D1 | Topology: cloud brain + phone shell, laptop-only, or Python on-device | Cloud brain + phone shell |
| D2 | Shell stack: native Kotlin or Flutter | Native Kotlin |
| D3 | Voice path: direct with ephemeral token (relay fallback) or relay-only | Direct, with relay fallback |
| D4 | Accountability intensity per domain and quiet hours | Start at L2 for academics, L1 elsewhere; you set the hours |
| D5 | Hands-free: wake word first or push-to-talk first | Push-to-talk and notification action first, wake word in M7 |
| D6 | Memory: reuse your existing engine or build fresh to section 5 | Reuse if it passes the freshness and provenance requirements |
| D7 | Database: SQLite with replication, or Postgres | SQLite while single-user |
