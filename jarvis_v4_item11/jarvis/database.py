"""
Jarvis v4 — data layer (butler.db)
=================================
Single-file, drop-in replacement for the v3-era database.py. Every name that
tools.py / server.py already imports still exists with the same signature:
    get_conn, init_db, is_setup_complete, now_pkt, append_episodic_turn,
    get_recent_episodic_turns, get_working_state
All v3/v4-wizard tables keep their existing columns; new columns have defaults,
so the current INSERT statements in server.py and tools.py keep working.

Layout is flat for speed, but sections are marked so the audit's §9 package
split (core/ memory/ planner/ proactive/ learning/ packs/ ops/) is a copy-paste.

Audit findings closed here
--------------------------
B1  atomic writes: tx() commits-or-rolls-back-and-closes; multi-write ops are one transaction.
B2  tasks_for_prompt(): NULL slots sort LAST, no silent LIMIT-10 crowding.
B4  migrate_legacy_v3(): backup + backfill slots/deadlines + dedupe + timestamp normalisation.
B5  resolve_deadline(): today / tomorrow 9am / Nov 18 / next week / weekday==today all parse.
B9  one time rule: UTC ISO-8601 stored, Asia/Karachi rendered; parse_ts() reads every legacy format.
B10 no dead schema: every table below has a writer function in this file.
B12 FKs on, indexes, CHECK constraints, schema_migrations, connections closed on exceptions.
G1  layered memory: sessions/turns/session_summaries + facts + rules + state_signals (no 40-row cap).
G2  capture: checkins (incl. resulting_focus), outcomes, study_sessions, learned_params.
G3  identity/targets/grading/strategy are rows (profile, courses, assessments, grading_scale, rules).
G4  life domains: domains + fitness/finance/journal/habits/people packs.
G5  state carries observed_at + ttl_minutes -> age/staleness is computable, never assumed fresh.
G6  assessments as rows -> required-final / feasibility maths has real inputs.
G7  self-model: tool_specs + capabilities + proposals (declarative, data-defined extension).
G8  accountability ledger: commitments -> outcomes -> reviews -> learned_params (reversible).
§5  fact record has provenance, confidence, importance, freshness, supersedes, sensitivity, status.
§6  full data model incl. plan_items, calendar_events, triggers, nudges, quiet_hours, channels,
    audit_log, eval_runs, schema_migrations. FTS5 retrieval with LIKE fallback.

Row factory supports .get(key, default) — sqlite3.Row does not, which is why
find_best_slot_for_task / add_task / log_syllabus_and_schedule currently raise
AttributeError. Fixed here, at the connection, so tools.py needs no edit.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

# ============================== core / clock.py ==============================
PKT = ZoneInfo("Asia/Karachi")
UTC = timezone.utc
DB_PATH = Path(__file__).resolve().parent / "butler.db"
BACKUP_DIR = DB_PATH.parent / "backups"
SCHEMA_VERSION = 1
RAW_TURN_RETENTION_DAYS = 30
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def utc_now() -> datetime:
    return datetime.now(UTC)


def now_pkt() -> datetime:
    """Back-compat name. Returns aware PKT datetime."""
    return datetime.now(PKT)


def iso_utc(dt: Optional[datetime] = None) -> str:
    """Canonical storage format: UTC ISO-8601 with offset."""
    dt = dt or utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_ts(value: Any, naive_zone: Any = UTC) -> Optional[datetime]:
    """Read every format this project has ever written (B9). Aware datetime or None.
    Bare strings are assumed UTC (SQLite CURRENT_TIMESTAMP) unless the column is PKT-named."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=naive_zone)
    s = str(value).strip()
    zone = None
    for tag, z in (("PKT", PKT), ("UTC", UTC), ("GMT", UTC)):
        if s.endswith(" " + tag):
            s, zone = s[: -(len(tag) + 1)].strip(), z
            break
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=zone or naive_zone)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            local_date = fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")
            return dt.replace(tzinfo=zone or (PKT if local_date else naive_zone))
        except ValueError:
            continue
    return None


def to_pkt(value: Any) -> Optional[datetime]:
    dt = parse_ts(value)
    return dt.astimezone(PKT) if dt else None


def fmt_pkt(value: Any, fmt: str = "%A, %d %b %Y • %I:%M %p PKT") -> str:
    dt = to_pkt(value)
    return dt.strftime(fmt) if dt else "unknown"


def minutes_since(value: Any) -> Optional[float]:
    dt = parse_ts(value)
    return round((utc_now() - dt).total_seconds() / 60, 1) if dt else None


def resolve_deadline(hint: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """B5: natural-language deadline -> aware PKT datetime. None only if truly unparseable.
    No time given -> 23:59 local. Weekday equal to today stays today unless its time passed."""
    if not hint:
        return None
    now = (now or now_pkt()).astimezone(PKT)
    s = str(hint).strip().lower()
    hour, minute = 23, 59
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
    else:
        m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", s)
        if m:
            hour, minute = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" else 0), 0
    explicit_time = m is not None
    base: Optional[datetime] = None
    if "today" in s or "tonight" in s or "eod" in s:
        base = now
    elif "tomorrow" in s:
        base = now + timedelta(days=1)
    elif "day after" in s:
        base = now + timedelta(days=2)
    elif "next week" in s:
        base = now + timedelta(days=(7 - now.weekday()))
    elif (m := re.search(r"\bin\s+(\d+)\s*(day|week|hour)", s)):
        n = int(m.group(1))
        base = now + timedelta(hours=n) if m.group(2) == "hour" else now + timedelta(days=n * (7 if m.group(2) == "week" else 1))
    if base is None:
        for idx, name in enumerate(DAYS):
            if name.lower()[:3] in s:
                ahead = (idx - now.weekday()) % 7
                base = now + timedelta(days=ahead)
                if ahead == 0 and explicit_time and base.replace(hour=hour, minute=minute) <= now:
                    base += timedelta(days=7)
                break
    if base is None:
        if (m := re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)):
            base = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=PKT)
        elif (m := re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2})\b|\b(\d{1,2})\s+([a-z]{3,9})\b", s)):
            mon = MONTHS.get((m.group(1) or m.group(4) or "")[:3])
            day = int(m.group(2) or m.group(3) or 0)
            if mon and 1 <= day <= 31:
                base = datetime(now.year, mon, day, tzinfo=PKT)
                if base.date() < now.date():
                    base = base.replace(year=now.year + 1)
    if base is None:
        return None
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ============================== core / db.py =================================
class DictRow(sqlite3.Row):
    """sqlite3.Row plus .get() — keeps dict-style tool code working."""

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (IndexError, KeyError):
            return default

    def as_dict(self) -> Dict[str, Any]:
        return {k: self[k] for k in self.keys()}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = DictRow
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


# N7 self-heal: if butler.db is deleted or replaced while the server is live,
# rebuild the schema on the next connection instead of crashing with
# "no such table: system_config". One cheap sentinel lookup per connection.
_SCHEMA_READY = False   # fast path, set once init_db has succeeded
_HEALING = False        # re-entrancy guard: init_db itself opens connections


def get_conn() -> sqlite3.Connection:
    global _SCHEMA_READY
    conn = _connect()
    if _HEALING:
        return conn
    if _SCHEMA_READY and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_config'").fetchone():
        return conn
    conn.close()
    _heal_schema()
    return _connect()


def _heal_schema() -> None:
    global _SCHEMA_READY, _HEALING
    _HEALING = True
    try:
        init_db()          # idempotent: IF NOT EXISTS + INSERT OR IGNORE throughout
        _SCHEMA_READY = True
    finally:
        _HEALING = False


@contextmanager
def tx():
    """B1/B12: one transaction, committed on success, rolled back AND closed on error."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(sql: str, args: Iterable = ()) -> List[Dict[str, Any]]:
    with tx() as c:
        return [r.as_dict() for r in c.execute(sql, tuple(args)).fetchall()]


def row(sql: str, args: Iterable = ()) -> Optional[Dict[str, Any]]:
    with tx() as c:
        r = c.execute(sql, tuple(args)).fetchone()
        return r.as_dict() if r else None


def scalar(sql: str, args: Iterable = (), default: Any = None) -> Any:
    with tx() as c:
        r = c.execute(sql, tuple(args)).fetchone()
        return r[0] if r and r[0] is not None else default


def write(sql: str, args: Iterable = ()) -> int:
    with tx() as c:
        cur = c.execute(sql, tuple(args))
        return cur.lastrowid


# ============================== ops / audit.py ===============================
def audit(action: str, table: str, row_id: Any = None, before: Any = None, after: Any = None,
          actor: str = "jarvis", turn_id: Optional[int] = None, undo: Any = None) -> None:
    """Every write is attributable and (where possible) reversible."""
    try:
        write("""INSERT INTO audit_log (ts_utc,actor,action,table_name,row_id,before_json,after_json,turn_id,undo_json)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
              (iso_utc(), actor, action, table, str(row_id) if row_id is not None else None,
               json.dumps(before) if before is not None else None,
               json.dumps(after) if after is not None else None, turn_id,
               json.dumps(undo) if undo is not None else None))
    except sqlite3.Error:
        pass  # never let bookkeeping break a user action


# ============================== schema (migration 0001) ======================
SCHEMA_0001 = """
-- ---------- operations ----------
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, actor TEXT DEFAULT 'jarvis',
  action TEXT NOT NULL, table_name TEXT, row_id TEXT, before_json TEXT, after_json TEXT,
  turn_id INTEGER, undo_json TEXT);
CREATE TABLE IF NOT EXISTS eval_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, scenario TEXT, passed INTEGER DEFAULT 0,
  detail_json TEXT, duration_ms INTEGER, git_ref TEXT, created_at TEXT NOT NULL);

-- ---------- identity / config ----------
CREATE TABLE IF NOT EXISTS system_config (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS profile (            -- profile_core: one editable identity source (G3)
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT NOT NULL, preferred_name TEXT,
  university TEXT NOT NULL DEFAULT '', program TEXT NOT NULL DEFAULT '', semester TEXT,
  timezone TEXT DEFAULT 'Asia/Karachi', current_gpa REAL, target_gpa REAL DEFAULT 4.0,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS domains (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, enabled INTEGER DEFAULT 0,
  pack TEXT, manifest_json TEXT, installed_at TEXT);

-- ---------- memory: semantic / procedural ----------
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL DEFAULT 'general',
  key TEXT NOT NULL, value TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'stated' CHECK (source IN ('stated','observed','inferred')),
  evidence TEXT, confidence REAL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
  importance INTEGER DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
  created_at TEXT NOT NULL, last_verified_at TEXT, verify_after_days REAL DEFAULT 30,
  supersedes INTEGER REFERENCES facts(id), retired_at TEXT,
  sensitivity TEXT DEFAULT 'normal' CHECK (sensitivity IN ('normal','private','vault')),
  status TEXT DEFAULT 'active' CHECK (status IN ('active','proposed','retired')),
  updated_at TEXT, turn_id INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS ux_facts_active ON facts(domain, key) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS freshness_policy (
  class TEXT PRIMARY KEY, verify_after_days REAL NOT NULL, ttl_minutes INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT DEFAULT 'general', key TEXT NOT NULL,
  body TEXT NOT NULL, version INTEGER DEFAULT 1,
  status TEXT DEFAULT 'active' CHECK (status IN ('active','proposed','retired')),
  supersedes INTEGER REFERENCES rules(id), source TEXT DEFAULT 'stated',
  created_at TEXT NOT NULL, approved_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rules_active ON rules(domain, key) WHERE status = 'active';

-- ---------- memory: episodic ----------
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_key TEXT UNIQUE NOT NULL,
  channel TEXT DEFAULT 'voice', model TEXT, started_at TEXT NOT NULL, ended_at TEXT,
  last_activity_at TEXT, turn_count INTEGER DEFAULT 0, live_seconds REAL DEFAULT 0,
  resumption_handle TEXT, goaway_count INTEGER DEFAULT 0, close_reason TEXT);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  turn_index INTEGER, role TEXT NOT NULL CHECK (role IN ('user','jarvis','tool','system')),
  content TEXT NOT NULL, ts_utc TEXT NOT NULL,
  interrupted INTEGER DEFAULT 0, client_turn_id TEXT,
  tool_name TEXT, tool_args_json TEXT, tool_result_json TEXT, latency_ms INTEGER,
  sensitivity TEXT DEFAULT 'normal', off_record INTEGER DEFAULT 0,
  UNIQUE (session_id, client_turn_id));
CREATE TABLE IF NOT EXISTS session_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
  summary TEXT NOT NULL, open_threads TEXT, facts_extracted INTEGER DEFAULT 0, created_at TEXT NOT NULL);

-- ---------- memory: fast state (G5) ----------
CREATE TABLE IF NOT EXISTS state_signals (
  key TEXT PRIMARY KEY, value TEXT, numeric_value REAL, ttl_minutes INTEGER DEFAULT 180,
  observed_at TEXT NOT NULL, source TEXT DEFAULT 'stated', confidence REAL DEFAULT 1.0);
CREATE TABLE IF NOT EXISTS working_state (      -- kept for tools.py compatibility
  id INTEGER PRIMARY KEY CHECK (id = 1),
  location TEXT NOT NULL DEFAULT 'Home',
  energy INTEGER NOT NULL DEFAULT 3 CHECK (energy BETWEEN 1 AND 5),
  hydration INTEGER NOT NULL DEFAULT 3 CHECK (hydration BETWEEN 0 AND 5),
  fed INTEGER NOT NULL DEFAULT 1 CHECK (fed IN (0,1)),
  stress INTEGER NOT NULL DEFAULT 2 CHECK (stress BETWEEN 1 AND 5),
  current_activity TEXT DEFAULT 'Ready', updated_at TEXT, updated_at_utc TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, location TEXT, energy INTEGER,
  hydration INTEGER, fed INTEGER, stress INTEGER, activity TEXT, notes TEXT,
  resulting_focus TEXT, analysed INTEGER DEFAULT 0, session_id INTEGER);

-- ---------- planning (domain-agnostic) ----------
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, title TEXT NOT NULL, why TEXT, metric TEXT,
  deadline TEXT, priority TEXT DEFAULT 'Medium',
  status TEXT DEFAULT 'active' CHECK (status IN ('active','paused','done','dropped')),
  target_value REAL, current_value REAL, unit TEXT, review_after_days REAL DEFAULT 30,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT, deadline TEXT,
  status TEXT DEFAULT 'active', domain TEXT DEFAULT 'Projects', goal_id INTEGER REFERENCES goals(id),
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, course_code TEXT, title TEXT NOT NULL,
  est_mins INTEGER DEFAULT 45 CHECK (est_mins > 0),
  status TEXT DEFAULT 'pending' CHECK (status IN
    ('pending','scheduled','in_progress','completed','missed','cancelled','duplicate')),
  is_surprise_quiz INTEGER DEFAULT 0 CHECK (is_surprise_quiz IN (0,1)),
  due_date TEXT, deadline_at TEXT,
  assigned_day TEXT, assigned_date TEXT, assigned_window TEXT, assigned_location TEXT,
  created_at TEXT, updated_at TEXT, completed_at TEXT,
  domain TEXT DEFAULT 'Academics', project_id INTEGER REFERENCES projects(id),
  goal_id INTEGER REFERENCES goals(id),
  energy_need INTEGER DEFAULT 3 CHECK (energy_need BETWEEN 1 AND 5), location_need TEXT,
  depends_on TEXT, priority INTEGER DEFAULT 2, rollover_count INTEGER DEFAULT 0,
  needs_slot INTEGER DEFAULT 0, notes TEXT, turn_id INTEGER);
CREATE TABLE IF NOT EXISTS time_blocks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day_of_week TEXT NOT NULL, start_time TEXT NOT NULL,
  end_time TEXT NOT NULL, type TEXT DEFAULT 'free', location TEXT, course_focus TEXT, guardrail TEXT,
  energy_profile INTEGER DEFAULT 3, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS routine_anchors (
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, time TEXT NOT NULL,
  label TEXT, days TEXT DEFAULT 'daily');
CREATE TABLE IF NOT EXISTS calendar_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, start_utc TEXT NOT NULL, end_utc TEXT,
  location TEXT, domain TEXT, kind TEXT, source TEXT DEFAULT 'stated', task_id INTEGER, notes TEXT);
CREATE TABLE IF NOT EXISTS plan_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
  block_id INTEGER REFERENCES time_blocks(id), date TEXT NOT NULL,
  start_time TEXT, end_time TEXT, location TEXT, version INTEGER DEFAULT 1,
  status TEXT DEFAULT 'planned' CHECK (status IN ('planned','done','partial','missed','moved','cancelled')),
  superseded_by INTEGER REFERENCES plan_items(id), explain_json TEXT, created_at TEXT NOT NULL);

-- ---------- accountability / learning (G8, P6) ----------
CREATE TABLE IF NOT EXISTS commitments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT DEFAULT 'Academics', promise TEXT NOT NULL,
  task_id INTEGER REFERENCES tasks(id), plan_item_id INTEGER REFERENCES plan_items(id),
  made_at TEXT NOT NULL, due_at TEXT, ladder_level INTEGER DEFAULT 1 CHECK (ladder_level BETWEEN 1 AND 4),
  status TEXT DEFAULT 'open' CHECK (status IN ('open','kept','broken','renegotiated')),
  turn_id INTEGER, closed_at TEXT);
CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, commitment_id INTEGER REFERENCES commitments(id),
  plan_item_id INTEGER REFERENCES plan_items(id), task_id INTEGER REFERENCES tasks(id),
  result TEXT NOT NULL CHECK (result IN ('done','partial','missed')), reason TEXT,
  actual_mins INTEGER, estimate_error_mins INTEGER, recorded_at TEXT NOT NULL, turn_id INTEGER);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT CHECK (kind IN ('daily','weekly')),
  period_start TEXT, period_end TEXT, adherence REAL, estimation_error REAL,
  body_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learned_params (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, domain TEXT DEFAULT 'general',
  value REAL NOT NULL, prev_value REAL, confidence REAL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  decay_halflife_days REAL DEFAULT 30, evidence_json TEXT,
  status TEXT DEFAULT 'proposed' CHECK (status IN ('proposed','active','rolled_back')),
  applied_at TEXT, rolled_back_at TEXT, review_id INTEGER REFERENCES reviews(id));

-- ---------- proactive (P5) ----------
CREATE TABLE IF NOT EXISTS triggers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
  condition_json TEXT, action_json TEXT, domain TEXT DEFAULT '*', enabled INTEGER DEFAULT 1,
  ladder_level INTEGER DEFAULT 1, cooldown_mins INTEGER DEFAULT 60, created_at TEXT);
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
  kind TEXT CHECK (kind IN ('voice','hud','push','sms','email')), target TEXT,
  enabled INTEGER DEFAULT 1, priority INTEGER DEFAULT 2);
CREATE TABLE IF NOT EXISTS quiet_hours (
  id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT DEFAULT '*', start_time TEXT NOT NULL,
  end_time TEXT NOT NULL, days TEXT DEFAULT 'daily', enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS nudges (
  id INTEGER PRIMARY KEY AUTOINCREMENT, trigger_id INTEGER REFERENCES triggers(id),
  task_id INTEGER, commitment_id INTEGER, channel_id INTEGER REFERENCES channels(id),
  scheduled_at TEXT NOT NULL, sent_at TEXT, seen_at TEXT, acted_at TEXT, snoozed_until TEXT,
  status TEXT DEFAULT 'pending' CHECK (status IN
    ('pending','sent','seen','acted','snoozed','suppressed','cancelled')),
  payload_json TEXT, reason TEXT);

-- ---------- extension / self-model (G7, §7.1) ----------
CREATE TABLE IF NOT EXISTS tool_specs (
  name TEXT PRIMARY KEY, description TEXT NOT NULL, params_json TEXT NOT NULL DEFAULT '{}',
  handler TEXT, risk TEXT DEFAULT 'read' CHECK (risk IN ('read','write','destructive')),
  confirmation TEXT DEFAULT 'never' CHECK (confirmation IN ('never','always','if_destructive')),
  behaviour TEXT DEFAULT 'BLOCKING' CHECK (behaviour IN ('BLOCKING','NON_BLOCKING')),
  scheduling TEXT DEFAULT 'WHEN_IDLE' CHECK (scheduling IN ('INTERRUPT','WHEN_IDLE','SILENT')),
  domain TEXT DEFAULT '*', enabled INTEGER DEFAULT 1, version INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS capabilities (
  key TEXT PRIMARY KEY, value_json TEXT, verified INTEGER DEFAULT 0, probed_at TEXT);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT CHECK (kind IN ('tool','rule','param','fact')),
  payload_json TEXT NOT NULL, evidence_json TEXT,
  status TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','applied')),
  created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT);

-- ---------- academics pack ----------
CREATE TABLE IF NOT EXISTS courses (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, instructor TEXT, credits INTEGER DEFAULT 3,
  nature TEXT DEFAULT 'Mixed', priority_tier INTEGER DEFAULT 2, time_share_pct REAL DEFAULT 10.0,
  difficulty INTEGER DEFAULT 3, study_protocol TEXT, risk_flags TEXT,
  domain TEXT DEFAULT 'Academics', active INTEGER DEFAULT 1, updated_at TEXT);
CREATE TABLE IF NOT EXISTS assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, course_code TEXT NOT NULL REFERENCES courses(code) ON DELETE CASCADE,
  label TEXT, type TEXT NOT NULL, max_marks REAL NOT NULL CHECK (max_marks >= 0),
  obtained REAL DEFAULT 0 CHECK (obtained >= 0), expected REAL DEFAULT 0 CHECK (expected >= 0),
  status TEXT DEFAULT 'Upcoming' CHECK (status IN ('Upcoming','In Progress','Completed','Missed','Cancelled')),
  weight_pct REAL, due_at TEXT, recorded_at TEXT, turn_id INTEGER);
CREATE TABLE IF NOT EXISTS grading_scale (
  threshold REAL PRIMARY KEY CHECK (threshold BETWEEN 0 AND 100),
  letter TEXT NOT NULL, points REAL NOT NULL CHECK (points BETWEEN 0 AND 5));
CREATE TABLE IF NOT EXISTS syllabus_progress (   -- syllabus_items
  id INTEGER PRIMARY KEY AUTOINCREMENT, course_code TEXT NOT NULL, chapter_topic TEXT NOT NULL,
  taught_scope TEXT NOT NULL, personal_target TEXT NOT NULL,
  self_study_status TEXT DEFAULT 'not_started' CHECK (self_study_status IN
    ('not_started','in_progress','done','verified')),
  deadline_label TEXT, deadline_at TEXT, updated_at TEXT, created_at TEXT, lecture_log_id INTEGER);
CREATE TABLE IF NOT EXISTS study_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, course_code TEXT, topic TEXT,
  method_used TEXT, duration_mins INTEGER, retention_confidence INTEGER DEFAULT 3,
  started_at TEXT, ended_at TEXT, plan_item_id INTEGER, outcome TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS spaced_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, course_code TEXT NOT NULL, item_key TEXT NOT NULL,
  topic TEXT, ease REAL DEFAULT 2.5, interval_days REAL DEFAULT 1, reps INTEGER DEFAULT 0,
  due_at TEXT, last_reviewed_at TEXT, UNIQUE (course_code, item_key));
CREATE TABLE IF NOT EXISTS lecture_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, course_code TEXT, topic TEXT, taught_scope TEXT,
  confusion_points TEXT, instructor_hints TEXT, debrief_json TEXT, logged_at TEXT NOT NULL, session_id INTEGER);
CREATE TABLE IF NOT EXISTS sandbox_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, kind TEXT, course_code TEXT,
  payload_json TEXT, result TEXT);

-- ---------- life packs ----------
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, relation TEXT, notes TEXT,
  sensitivity TEXT DEFAULT 'normal', last_contact_at TEXT, verify_after_days REAL DEFAULT 90, created_at TEXT);
CREATE TABLE IF NOT EXISTS habits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT DEFAULT 'Habits', name TEXT NOT NULL,
  cadence TEXT DEFAULT 'daily', target_per_week INTEGER DEFAULT 7, cue TEXT,
  status TEXT DEFAULT 'active', created_at TEXT);
CREATE TABLE IF NOT EXISTS habit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, habit_id INTEGER REFERENCES habits(id) ON DELETE CASCADE,
  date TEXT NOT NULL, done INTEGER DEFAULT 1, note TEXT, UNIQUE (habit_id, date));
CREATE TABLE IF NOT EXISTS body_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, metric TEXT NOT NULL,
  value REAL NOT NULL, unit TEXT, source TEXT DEFAULT 'stated');
CREATE TABLE IF NOT EXISTS workouts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, kind TEXT, duration_mins INTEGER,
  intensity INTEGER, notes TEXT);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL,
  kind TEXT CHECK (kind IN ('income','expense','transfer')), category TEXT,
  amount REAL NOT NULL, currency TEXT DEFAULT 'PKR', account TEXT, note TEXT,
  sensitivity TEXT DEFAULT 'private');
CREATE TABLE IF NOT EXISTS budgets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, period TEXT DEFAULT 'monthly',
  limit_amount REAL NOT NULL, currency TEXT DEFAULT 'PKR');
CREATE TABLE IF NOT EXISTS journal_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, mood INTEGER, body TEXT NOT NULL,
  tags TEXT, sensitivity TEXT DEFAULT 'private', off_record INTEGER DEFAULT 0);

-- ---------- hot-path indexes (B12) ----------
CREATE INDEX IF NOT EXISTS ix_turns_session ON turns(session_id, id);
CREATE INDEX IF NOT EXISTS ix_turns_ts ON turns(ts_utc);
CREATE INDEX IF NOT EXISTS ix_facts_rank ON facts(status, importance DESC, last_verified_at);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status, deadline_at);
CREATE INDEX IF NOT EXISTS ix_tasks_slot ON tasks(assigned_date, assigned_window);
CREATE INDEX IF NOT EXISTS ix_tasks_dedupe ON tasks(course_code, title, status);
CREATE INDEX IF NOT EXISTS ix_syllabus_dedupe ON syllabus_progress(course_code, chapter_topic, taught_scope);
CREATE INDEX IF NOT EXISTS ix_blocks_day ON time_blocks(day_of_week, start_time);
CREATE INDEX IF NOT EXISTS ix_plan_date ON plan_items(date, status);
CREATE INDEX IF NOT EXISTS ix_assess_course ON assessments(course_code, status);
CREATE INDEX IF NOT EXISTS ix_nudges_due ON nudges(status, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_commit_open ON commitments(status, due_at);
CREATE INDEX IF NOT EXISTS ix_outcomes_time ON outcomes(recorded_at);
CREATE INDEX IF NOT EXISTS ix_audit_time ON audit_log(ts_utc);
CREATE INDEX IF NOT EXISTS ix_signals_age ON state_signals(observed_at);
CREATE INDEX IF NOT EXISTS ix_reviews_due ON spaced_reviews(due_at);

-- ---------- delete trails: tools.py deletes bypass helpers, so log at the DB ----------
CREATE TRIGGER IF NOT EXISTS trg_audit_task_del AFTER DELETE ON tasks BEGIN
  INSERT INTO audit_log (ts_utc,actor,action,table_name,row_id,before_json,undo_json)
  VALUES (strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'db','delete','tasks',OLD.id,
    json_object('id',OLD.id,'title',OLD.title,'course_code',OLD.course_code,'status',OLD.status),
    json_object('restore_sql','INSERT INTO tasks (id,course_code,title,est_mins,status) VALUES ('
      || OLD.id || ',' || quote(OLD.course_code) || ',' || quote(OLD.title) || ',' || OLD.est_mins || ',' || quote(OLD.status) || ')'));
END;
CREATE TRIGGER IF NOT EXISTS trg_audit_assess_del AFTER DELETE ON assessments BEGIN
  INSERT INTO audit_log (ts_utc,actor,action,table_name,row_id,before_json)
  VALUES (strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'db','delete','assessments',OLD.id,
    json_object('course_code',OLD.course_code,'label',OLD.label,'type',OLD.type,
                'max_marks',OLD.max_marks,'obtained',OLD.obtained,'status',OLD.status));
END;
CREATE TRIGGER IF NOT EXISTS trg_audit_assess_upd AFTER UPDATE OF obtained,expected,status ON assessments BEGIN
  INSERT INTO audit_log (ts_utc,actor,action,table_name,row_id,before_json,after_json)
  VALUES (strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'db','update','assessments',OLD.id,
    json_object('obtained',OLD.obtained,'expected',OLD.expected,'status',OLD.status),
    json_object('obtained',NEW.obtained,'expected',NEW.expected,'status',NEW.status));
END;
CREATE TRIGGER IF NOT EXISTS trg_audit_course_del AFTER DELETE ON courses BEGIN
  INSERT INTO audit_log (ts_utc,actor,action,table_name,row_id,before_json)
  VALUES (strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'db','delete','courses',OLD.code,
    json_object('code',OLD.code,'name',OLD.name,'credits',OLD.credits));
END;
-- tools.py updates working_state without a UTC stamp; fill it, but never clobber an explicit one.
CREATE TRIGGER IF NOT EXISTS trg_ws_utc AFTER UPDATE ON working_state
WHEN NEW.updated_at_utc IS NULL OR NEW.updated_at_utc IS OLD.updated_at_utc BEGIN
  UPDATE working_state SET updated_at_utc = strftime('%Y-%m-%dT%H:%M:%S+00:00','now') WHERE id = 1;
END;

-- ---------- back-compat: episodic_memory becomes a view over the turn ledger ----------
CREATE VIEW IF NOT EXISTS episodic_memory AS
  SELECT id, strftime('%Y-%m-%d %H:%M PKT', ts_utc, '+5 hours') AS timestamp_pkt, role, content
  FROM turns WHERE off_record = 0 ORDER BY id;
CREATE TRIGGER IF NOT EXISTS trg_episodic_insert INSTEAD OF INSERT ON episodic_memory BEGIN
  INSERT INTO turns (session_id, role, content, ts_utc)
  VALUES ((SELECT id FROM sessions WHERE session_key = 'legacy'),
          CASE WHEN NEW.role IN ('user','jarvis','tool','system') THEN NEW.role ELSE 'system' END,
          NEW.content, strftime('%Y-%m-%dT%H:%M:%S+00:00','now'));
END;
"""

FTS_SQL = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(key, value, content='facts', content_rowid='id')",
    """CREATE TRIGGER IF NOT EXISTS trg_facts_ai AFTER INSERT ON facts BEGIN
         INSERT INTO facts_fts(rowid,key,value) VALUES (NEW.id,NEW.key,NEW.value); END""",
    """CREATE TRIGGER IF NOT EXISTS trg_facts_ad AFTER DELETE ON facts BEGIN
         INSERT INTO facts_fts(facts_fts,rowid,key,value) VALUES ('delete',OLD.id,OLD.key,OLD.value); END""",
    """CREATE TRIGGER IF NOT EXISTS trg_facts_au AFTER UPDATE ON facts BEGIN
         INSERT INTO facts_fts(facts_fts,rowid,key,value) VALUES ('delete',OLD.id,OLD.key,OLD.value);
         INSERT INTO facts_fts(rowid,key,value) VALUES (NEW.id,NEW.key,NEW.value); END""",
    "CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(content, content='turns', content_rowid='id')",
    """CREATE TRIGGER IF NOT EXISTS trg_turns_ai AFTER INSERT ON turns BEGIN
         INSERT INTO turns_fts(rowid,content) VALUES (NEW.id,NEW.content); END""",
    """CREATE TRIGGER IF NOT EXISTS trg_turns_ad AFTER DELETE ON turns BEGIN
         INSERT INTO turns_fts(turns_fts,rowid,content) VALUES ('delete',OLD.id,OLD.content); END""",
    "CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(body, content='journal_entries', content_rowid='id')",
    """CREATE TRIGGER IF NOT EXISTS trg_journal_ai AFTER INSERT ON journal_entries BEGIN
         INSERT INTO journal_fts(rowid,body) VALUES (NEW.id,NEW.body); END""",
]

DEFAULT_CONFIG = {
    "setup_complete": "false", "voice_name": "Fenrir", "butler_name": "Butler",
    "personality": "Formal Butler", "accountability_academics": "L2", "accountability_other": "L1",
    "quiet_start": "23:00", "quiet_end": "07:00", "nag_budget": "5", "rollover_cap": "45",
    "wake_word_pref": "Push-to-Talk", "privacy": "ask_sensitive",
    "live_model": "gemini-3.8-live", "text_model": "gemini-3.8",
    "live_minute_cap": "90", "session_idle_close_secs": "120", "fact_reask_cap": "3",
    "raw_turn_retention_days": str(RAW_TURN_RETENTION_DAYS),
}
DEFAULT_FACT_POLICY = [  # §5 freshness defaults, all editable rows
    ("location", 0.01, 240, "hours"), ("energy", 0.125, 180, "~3 hours"),
    ("hunger", 0.125, 180, "~3 hours"), ("fed", 0.125, 180, "~3 hours"),
    ("stress", 0.125, 180, "~3 hours"), ("schedule", 30, None, "weekly template"),
    ("goals", 30, None, ""), ("body", 14, None, "body metrics"),
    ("grades", 7, None, "re-verify after each assessment"),
    ("relationships", 90, None, ""), ("identity", 180, None, "core identity"),
    ("default", 30, None, "fallback"),
]
DEFAULT_DOMAINS = [("Academics", 1, "academics"), ("Fitness", 0, "fitness"), ("Projects", 0, "projects"),
                   ("Finance", 0, "finance"), ("Journal", 0, "journal"), ("Habits", 0, "habits")]
DEFAULT_TRIGGERS = [  # §7.3
    ("pre_block", "schedule", {"minutes_before": 15}, {"action": "nudge", "include": "ticket"}, "*", 1, 30),
    ("block_end", "schedule", {"at": "block_end"}, {"action": "ask_outcome"}, "*", 2, 30),
    ("deadline_t24", "deadline", {"hours_before": 24}, {"action": "nudge_and_offer_replan"}, "*", 2, 360),
    ("deadline_t3", "deadline", {"hours_before": 3}, {"action": "nudge_and_offer_replan"}, "*", 2, 120),
    ("lecture_end", "schedule", {"at": "lecture_end"}, {"action": "ask_debrief"}, "Academics", 1, 60),
    ("stale_state", "freshness", {"older_than_ttl": True}, {"action": "ask_one_question"}, "*", 1, 180),
    ("stale_fact", "freshness", {"past_review_interval": True}, {"action": "ask_within_cap"}, "*", 1, 720),
    ("missed_block", "outcome", {"result": "missed"}, {"action": "offer_replan_within_cap"}, "*", 2, 60),
    ("evening_review", "review", {"at": "21:30"}, {"action": "run_daily_review"}, "*", 1, 720),
    ("weekly_audit", "review", {"weekday": "Sunday", "at": "18:00"}, {"action": "run_weekly_review"}, "*", 2, 1440),
    ("pattern", "learning", {"param_confidence_gte": 0.7}, {"action": "suggest_never_impose"}, "*", 1, 1440),
]
DEFAULT_CHANNELS = [("voice", "voice", None, 1, 1), ("hud", "hud", "web", 1, 2), ("push", "push", "android", 0, 3)]


def _apply_migrations() -> None:
    with tx() as c:
        c.executescript(SCHEMA_0001)
        for stmt in FTS_SQL:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # FTS5 unavailable -> search_* falls back to LIKE
        done = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
        if SCHEMA_VERSION not in done:
            c.execute("INSERT INTO schema_migrations (version,name,applied_at) VALUES (?,?,?)",
                      (SCHEMA_VERSION, "0001_v4_full_model", iso_utc()))


def _seed() -> None:
    with tx() as c:
        for k, v in DEFAULT_CONFIG.items():
            c.execute("INSERT OR IGNORE INTO system_config (key,value,updated_at) VALUES (?,?,?)", (k, v, iso_utc()))
        for cls, days, ttl, note in DEFAULT_FACT_POLICY:
            c.execute("INSERT OR IGNORE INTO freshness_policy (class,verify_after_days,ttl_minutes,note) VALUES (?,?,?,?)",
                      (cls, days, ttl, note))
        for name, enabled, pack in DEFAULT_DOMAINS:
            c.execute("INSERT OR IGNORE INTO domains (name,enabled,pack) VALUES (?,?,?)", (name, enabled, pack))
        for name, kind, cond, act, dom, lvl, cool in DEFAULT_TRIGGERS:
            c.execute("""INSERT OR IGNORE INTO triggers (name,kind,condition_json,action_json,domain,
                         ladder_level,cooldown_mins,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                      (name, kind, json.dumps(cond), json.dumps(act), dom, lvl, cool, iso_utc()))
        for name, kind, target, enabled, prio in DEFAULT_CHANNELS:
            c.execute("INSERT OR IGNORE INTO channels (name,kind,target,enabled,priority) VALUES (?,?,?,?,?)",
                      (name, kind, target, enabled, prio))
        if not c.execute("SELECT 1 FROM quiet_hours LIMIT 1").fetchone():
            c.execute("INSERT INTO quiet_hours (domain,start_time,end_time) VALUES ('*',?,?)",
                      (DEFAULT_CONFIG["quiet_start"], DEFAULT_CONFIG["quiet_end"]))
        if not c.execute("SELECT 1 FROM working_state WHERE id=1").fetchone():
            c.execute("""INSERT INTO working_state (id,location,energy,hydration,fed,stress,current_activity,
                         updated_at,updated_at_utc) VALUES (1,'Home',3,3,1,2,'Ready',?,?)""",
                      (now_pkt().strftime("%Y-%m-%d %H:%M:%S %Z"), iso_utc()))
        c.execute("INSERT OR IGNORE INTO sessions (session_key,channel,started_at) VALUES ('legacy','import',?)",
                  (iso_utc(),))
        c.execute("INSERT OR IGNORE INTO capabilities (key,value_json,verified,probed_at) VALUES ('schema_version',?,1,?)",
                  (json.dumps(SCHEMA_VERSION), iso_utc()))


def init_db() -> None:
    """Idempotent. Called by server.py at import, and by get_conn() if the DB vanishes (N7)."""
    global _SCHEMA_READY
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _apply_migrations()
    _seed()
    normalise_timestamps()
    _SCHEMA_READY = True


def schema_version() -> int:
    return int(scalar("SELECT MAX(version) FROM schema_migrations", default=0))


def is_setup_complete() -> bool:
    return scalar("SELECT value FROM system_config WHERE key='setup_complete'", default="false") == "true"


def get_config(key: str, default: Any = None) -> Any:
    return scalar("SELECT value FROM system_config WHERE key=?", (key,), default)


def set_config(key: str, value: Any, actor: str = "jarvis") -> None:
    before = get_config(key)
    write("INSERT INTO system_config (key,value,updated_at) VALUES (?,?,?) "
          "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
          (key, str(value), iso_utc()))
    audit("config_set", "system_config", key, before, str(value), actor)


def all_config() -> Dict[str, str]:
    return {r["key"]: r["value"] for r in rows("SELECT key,value FROM system_config")}


# ============================== memory / facts.py ============================
def _policy_days(domain: str, key: str) -> float:
    probe = f"{domain}.{key}".lower()
    for cls in (key.lower(), domain.lower(), probe.split(".")[0]):
        r = row("SELECT verify_after_days FROM freshness_policy WHERE class=?", (cls,))
        if r:
            return float(r["verify_after_days"])
    return float(scalar("SELECT verify_after_days FROM freshness_policy WHERE class='default'", default=30))


def remember(domain: str, key: str, value: str, source: str = "stated", evidence: Optional[str] = None,
             confidence: float = 1.0, importance: int = 3, sensitivity: str = "normal",
             verify_after_days: Optional[float] = None, turn_id: Optional[int] = None,
             actor: str = "jarvis") -> Dict[str, Any]:
    """§5 write path. Nothing is overwritten: the old row is retired and superseded.
    Inferred facts land as 'proposed' and must be confirmed by voice."""
    status = "proposed" if source == "inferred" else "active"
    days = verify_after_days if verify_after_days is not None else _policy_days(domain, key)
    now = iso_utc()
    with tx() as c:
        old = c.execute("SELECT * FROM facts WHERE domain=? AND key=? AND status='active'", (domain, key)).fetchone()
        old_id = old["id"] if old else None
        if old and status == "active":
            c.execute("UPDATE facts SET status='retired', retired_at=?, updated_at=? WHERE id=?", (now, now, old_id))
        elif old and status == "proposed":
            old_id = None  # keep the active fact; queue the candidate beside it
        cur = c.execute("""INSERT INTO facts (domain,key,value,source,evidence,confidence,importance,created_at,
                           last_verified_at,verify_after_days,supersedes,sensitivity,status,updated_at,turn_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (domain, key, str(value), source, evidence, confidence, importance, now,
                         now if status == "active" else None, days, old_id, sensitivity, status, now, turn_id))
        new_id = cur.lastrowid
    audit("remember", "facts", new_id,
          {"value": old["value"]} if old else None,
          {"domain": domain, "key": key, "value": str(value), "status": status}, actor, turn_id,
          undo={"retire_fact": new_id, "restore_fact": old_id})
    return {"fact_id": new_id, "status": status, "supersedes": old_id}


def confirm_fact(fact_id: int, value: Optional[str] = None, actor: str = "haider") -> Dict[str, Any]:
    """Voice confirmation: proposed -> active, supersedes the previous active row."""
    now = iso_utc()
    with tx() as c:
        f = c.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        if not f:
            raise ValueError(f"fact {fact_id} not found")
        prev = c.execute("SELECT id FROM facts WHERE domain=? AND key=? AND status='active' AND id<>?",
                         (f["domain"], f["key"], fact_id)).fetchone()
        if prev:
            c.execute("UPDATE facts SET status='retired', retired_at=? WHERE id=?", (now, prev["id"]))
        c.execute("""UPDATE facts SET status='active', value=COALESCE(?,value), supersedes=COALESCE(?,supersedes),
                     last_verified_at=?, updated_at=? WHERE id=?""", (value, prev["id"] if prev else None, now, now, fact_id))
    audit("confirm_fact", "facts", fact_id, None, {"value": value or f["value"]}, actor)
    return {"fact_id": fact_id, "status": "active"}


def verify_fact(fact_id: int) -> None:
    """An answer resets last_verified_at (§5 freshness)."""
    write("UPDATE facts SET last_verified_at=?, updated_at=? WHERE id=?", (iso_utc(), iso_utc(), fact_id))
    audit("verify_fact", "facts", fact_id)


def retire_fact(fact_id: int, reason: str = "superseded") -> None:
    write("UPDATE facts SET status='retired', retired_at=?, updated_at=? WHERE id=?", (iso_utc(), iso_utc(), fact_id))
    audit("retire_fact", "facts", fact_id, after={"reason": reason})


def get_facts(domain: Optional[str] = None, keys: Optional[List[str]] = None, include_private: bool = True,
              include_vault: bool = False, status: str = "active", limit: int = 200) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM facts WHERE status=?"
    args: List[Any] = [status]
    if domain:
        sql += " AND domain=?"
        args.append(domain)
    if keys:
        sql += f" AND key IN ({','.join('?' * len(keys))})"
        args += list(keys)
    if not include_vault:
        sql += " AND sensitivity<>'vault'"
    if not include_private:
        sql += " AND sensitivity='normal'"
    sql += " ORDER BY importance DESC, last_verified_at DESC LIMIT ?"
    args.append(limit)
    return rows(sql, args)


def search_facts(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """§5 read path: FTS5 first, LIKE fallback."""
    try:
        return rows("""SELECT f.* FROM facts_fts t JOIN facts f ON f.id=t.rowid
                       WHERE facts_fts MATCH ? AND f.status='active' AND f.sensitivity<>'vault'
                       ORDER BY rank LIMIT ?""", (query, limit))
    except sqlite3.OperationalError:
        like = f"%{query}%"
        return rows("""SELECT * FROM facts WHERE status='active' AND sensitivity<>'vault'
                       AND (key LIKE ? OR value LIKE ?) ORDER BY importance DESC LIMIT ?""", (like, like, limit))


def set_rule(domain: str, key: str, body: str, source: str = "stated", approved: bool = True) -> Dict[str, Any]:
    now = iso_utc()
    with tx() as c:
        prev = c.execute("SELECT id,version FROM rules WHERE domain=? AND key=? AND status='active'",
                         (domain, key)).fetchone()
        if prev and approved:
            c.execute("UPDATE rules SET status='retired' WHERE id=?", (prev["id"],))
        cur = c.execute("""INSERT INTO rules (domain,key,body,version,status,supersedes,source,created_at,approved_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (domain, key, body, (prev["version"] + 1) if prev else 1,
                         "active" if approved else "proposed", prev["id"] if prev else None, source, now,
                         now if approved else None))
        rid = cur.lastrowid
    audit("set_rule", "rules", rid, None, {"domain": domain, "key": key, "body": body})
    return {"rule_id": rid, "supersedes": prev["id"] if prev else None}


def get_rules(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    if domain:
        return rows("SELECT * FROM rules WHERE status='active' AND domain IN (?,'general') ORDER BY domain,key", (domain,))
    return rows("SELECT * FROM rules WHERE status='active' ORDER BY domain,key")


# ============================== memory / freshness.py ========================
def facts_due_for_review(limit: int = 20) -> List[Dict[str, Any]]:
    """Ranked by importance x staleness (§5 daily job)."""
    out = []
    for f in rows("SELECT * FROM facts WHERE status='active'"):
        last = parse_ts(f["last_verified_at"] or f["created_at"])
        if not last:
            continue
        age_days = (utc_now() - last).total_seconds() / 86400
        due = float(f["verify_after_days"] or 30)
        if age_days >= due:
            out.append({**f, "age_days": round(age_days, 2), "overdue_days": round(age_days - due, 2),
                        "priority": round(f["importance"] * (age_days / max(due, 0.01)), 2)})
    out.sort(key=lambda x: x["priority"], reverse=True)
    return out[:limit]


def stale_signals() -> List[Dict[str, Any]]:
    out = []
    for s in rows("SELECT * FROM state_signals"):
        age = minutes_since(s["observed_at"])
        if age is None or age > float(s["ttl_minutes"] or 180):
            out.append({**s, "age_minutes": age})
    return out


# ============================== memory / episodic.py =========================
def start_session(channel: str = "voice", model: Optional[str] = None,
                  session_key: Optional[str] = None) -> int:
    key = session_key or f"{channel}-{utc_now().strftime('%Y%m%dT%H%M%S')}"
    now = iso_utc()
    with tx() as c:
        c.execute("INSERT INTO sessions (session_key,channel,model,started_at,last_activity_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(session_key) DO UPDATE SET last_activity_at=excluded.last_activity_at",
                  (key, channel, model or get_config("live_model"), now, now))
        return c.execute("SELECT id FROM sessions WHERE session_key=?", (key,)).fetchone()[0]


def append_turn(session_id: int, role: str, content: str, client_turn_id: Optional[str] = None,
                interrupted: bool = False, tool_name: Optional[str] = None, tool_args: Any = None,
                tool_result: Any = None, latency_ms: Optional[int] = None,
                sensitivity: str = "normal", off_record: bool = False) -> Optional[int]:
    """Turn ledger: commits each turn exactly once (R4/§7.5). Empty or off-record turns are skipped."""
    clean = (content or "").strip()
    if not clean and not tool_name:
        return None
    if off_record:
        return None
    now = iso_utc()
    with tx() as c:
        if client_turn_id:
            exists = c.execute("SELECT id FROM turns WHERE session_id=? AND client_turn_id=?",
                               (session_id, client_turn_id)).fetchone()
            if exists:
                c.execute("UPDATE turns SET content=?, interrupted=?, ts_utc=? WHERE id=?",
                          (clean, int(interrupted), now, exists["id"]))
                return exists["id"]
        idx = c.execute("SELECT COALESCE(MAX(turn_index),0)+1 FROM turns WHERE session_id=?", (session_id,)).fetchone()[0]
        cur = c.execute("""INSERT INTO turns (session_id,turn_index,role,content,ts_utc,interrupted,client_turn_id,
                           tool_name,tool_args_json,tool_result_json,latency_ms,sensitivity,off_record)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (session_id, idx, role, clean or f"[tool:{tool_name}]", now, int(interrupted), client_turn_id,
                         tool_name, json.dumps(tool_args) if tool_args is not None else None,
                         json.dumps(tool_result) if tool_result is not None else None, latency_ms, sensitivity, 0))
        tid = cur.lastrowid
        c.execute("UPDATE sessions SET turn_count=turn_count+1, last_activity_at=? WHERE id=?", (now, session_id))
    return tid


def mark_turn_interrupted(turn_id: int) -> None:
    write("UPDATE turns SET interrupted=1 WHERE id=?", (turn_id,))


def end_session(session_id: int, reason: str = "idle", resumption_handle: Optional[str] = None,
                live_seconds: Optional[float] = None, goaway: bool = False) -> None:
    write("""UPDATE sessions SET ended_at=?, last_activity_at=?, close_reason=?,
             resumption_handle=COALESCE(?,resumption_handle),
             live_seconds=COALESCE(?,live_seconds), goaway_count=goaway_count+? WHERE id=?""",
          (iso_utc(), iso_utc(), reason, resumption_handle, live_seconds, 1 if goaway else 0, session_id))


def save_session_summary(session_id: int, summary: str, open_threads: Optional[str] = None) -> int:
    return write("""INSERT INTO session_summaries (session_id,summary,open_threads,created_at) VALUES (?,?,?,?)
                    ON CONFLICT(session_id) DO UPDATE SET summary=excluded.summary,
                    open_threads=excluded.open_threads, created_at=excluded.created_at""",
                 (session_id, summary, open_threads, iso_utc()))


def last_session_summary(exclude_session: Optional[int] = None) -> Optional[Dict[str, Any]]:
    return row("""SELECT s.*, se.session_key, se.channel, se.started_at FROM session_summaries s
                  JOIN sessions se ON se.id=s.session_id WHERE (? IS NULL OR s.session_id<>?)
                  ORDER BY s.created_at DESC LIMIT 1""", (exclude_session, exclude_session))


def recent_turns(limit: int = 6, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
    if session_id:
        r = rows("SELECT * FROM turns WHERE session_id=? AND off_record=0 ORDER BY id DESC LIMIT ?", (session_id, limit))
    else:
        r = rows("SELECT * FROM turns WHERE off_record=0 ORDER BY id DESC LIMIT ?", (limit,))
    return list(reversed(r))


def session_history(limit: int = 20) -> List[Dict[str, Any]]:
    return rows("""SELECT se.*, ss.summary FROM sessions se LEFT JOIN session_summaries ss ON ss.session_id=se.id
                   ORDER BY se.id DESC LIMIT ?""", (limit,))


def search_turns(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    try:
        return rows("""SELECT t.* FROM turns_fts f JOIN turns t ON t.id=f.rowid
                       WHERE turns_fts MATCH ? AND t.off_record=0 ORDER BY rank LIMIT ?""", (query, limit))
    except sqlite3.OperationalError:
        return rows("SELECT * FROM turns WHERE content LIKE ? AND off_record=0 ORDER BY id DESC LIMIT ?",
                    (f"%{query}%", limit))


def prune_raw_turns(days: Optional[int] = None) -> int:
    """Raw turns 30 days, summaries kept forever (§5). Replaces the old 40-row guillotine."""
    days = int(days or get_config("raw_turn_retention_days", RAW_TURN_RETENTION_DAYS))
    cutoff = iso_utc(utc_now() - timedelta(days=days))
    with tx() as c:
        n = c.execute("""DELETE FROM turns WHERE ts_utc < ? AND off_record=0 AND session_id IN
                         (SELECT session_id FROM session_summaries)""", (cutoff,)).rowcount
    audit("prune_raw_turns", "turns", after={"deleted": n, "cutoff": cutoff})
    return n


def append_episodic_turn(role: str, content: str) -> None:
    """Back-compat wrapper -> real turn ledger, no cap."""
    sid = start_session(session_key="legacy", channel="legacy")
    append_turn(sid, role if role in ("user", "jarvis", "tool", "system") else "system", content)


def get_recent_episodic_turns(limit: int = 12) -> List[Dict[str, Any]]:
    return [{"timestamp_pkt": fmt_pkt(t["ts_utc"], "%Y-%m-%d %H:%M PKT"), "role": t["role"], "content": t["content"]}
            for t in recent_turns(limit)]


# ============================== memory / state.py ============================
STATE_TTL = {"location": 240, "energy": 180, "hydration": 180, "fed": 180, "stress": 180, "current_activity": 60}


def set_signal(key: str, value: Any, ttl_minutes: Optional[int] = None, source: str = "stated",
               confidence: float = 1.0) -> None:
    ttl = ttl_minutes if ttl_minutes is not None else STATE_TTL.get(key, 180)
    numeric = float(value) if isinstance(value, (int, float)) else None
    write("""INSERT INTO state_signals (key,value,numeric_value,ttl_minutes,observed_at,source,confidence)
             VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,
             numeric_value=excluded.numeric_value, ttl_minutes=excluded.ttl_minutes,
             observed_at=excluded.observed_at, source=excluded.source, confidence=excluded.confidence""",
          (key, str(value), numeric, ttl, iso_utc(), source, confidence))


def get_signals(fresh_only: bool = True) -> Dict[str, Any]:
    out = {}
    for s in rows("SELECT * FROM state_signals"):
        age = minutes_since(s["observed_at"])
        fresh = age is not None and age <= float(s["ttl_minutes"] or 180)
        if fresh_only and not fresh:
            continue
        out[s["key"]] = {**s, "age_minutes": age, "fresh": fresh}
    return out


def set_working_state(location: Optional[str] = None, energy: Optional[int] = None,
                      hydration: Optional[int] = None, fed: Optional[int] = None,
                      stress: Optional[int] = None, current_activity: Optional[str] = None,
                      source: str = "stated") -> Dict[str, Any]:
    cur = get_working_state()
    vals = {
        "location": (location or cur["location"]).strip(),
        "energy": max(1, min(5, int(energy))) if energy is not None else cur["energy"],
        "hydration": max(0, min(5, int(hydration))) if hydration is not None else cur["hydration"],
        "fed": (1 if fed else 0) if fed is not None else cur["fed"],
        "stress": max(1, min(5, int(stress))) if stress is not None else cur["stress"],
        "current_activity": (current_activity or cur["current_activity"]).strip(),
    }
    with tx() as c:
        c.execute("""UPDATE working_state SET location=?,energy=?,hydration=?,fed=?,stress=?,current_activity=?,
                     updated_at=?, updated_at_utc=?, source=? WHERE id=1""",
                  (vals["location"], vals["energy"], vals["hydration"], vals["fed"], vals["stress"],
                   vals["current_activity"], now_pkt().strftime("%Y-%m-%d %H:%M:%S %Z"), iso_utc(), source))
        for k, v in vals.items():
            numeric = float(v) if isinstance(v, (int, float)) else None
            c.execute("""INSERT INTO state_signals (key,value,numeric_value,ttl_minutes,observed_at,source)
                         VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                         numeric_value=excluded.numeric_value, observed_at=excluded.observed_at, source=excluded.source""",
                      (k, str(v), numeric, STATE_TTL.get(k, 180), iso_utc(), source))
    audit("state_update", "working_state", 1, cur, vals)
    return get_working_state()


def get_working_state() -> Dict[str, Any]:
    """Back-compat shape + age/staleness so 'authoritative' is never assumed (G5)."""
    r = row("SELECT * FROM working_state WHERE id=1")
    if not r:
        r = {"location": "Home", "energy": 3, "hydration": 3, "fed": 1, "stress": 2,
             "current_activity": "Ready", "updated_at": now_pkt().strftime("%Y-%m-%d %H:%M:%S %Z"),
             "updated_at_utc": iso_utc()}
    age = minutes_since(r.get("updated_at_utc") or r.get("updated_at"))
    return {**r, "age_minutes": age, "is_stale": age is None or age > 180,
            "updated_at_pkt": fmt_pkt(r.get("updated_at_utc") or r.get("updated_at"), "%Y-%m-%d %H:%M PKT")}


def record_checkin(location: Optional[str] = None, energy: Optional[int] = None, hydration: Optional[int] = None,
                   fed: Optional[int] = None, stress: Optional[int] = None, activity: Optional[str] = None,
                   notes: Optional[str] = None, resulting_focus: Optional[str] = None,
                   session_id: Optional[int] = None) -> int:
    """G2: check-ins are stored AND analysable; resulting_focus finally has a writer."""
    cid = write("""INSERT INTO checkins (ts_utc,location,energy,hydration,fed,stress,activity,notes,
                   resulting_focus,session_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (iso_utc(), location, energy, hydration, fed, stress, activity, notes, resulting_focus, session_id))
    set_working_state(location, energy, hydration, fed, stress, activity, source="checkin")
    return cid


# ============================== planning / model.py ==========================
def tasks_for_prompt(limit: int = 12) -> List[Dict[str, Any]]:
    """B2: real deadlines first, unslotted legacy rows LAST — never crowding out today's plan."""
    return rows("""SELECT t.*, c.priority_tier AS course_tier, c.name AS course_name FROM tasks t
                   LEFT JOIN courses c ON t.course_code=c.code
                   WHERE t.status IN ('pending','scheduled','in_progress')
                   ORDER BY t.is_surprise_quiz DESC,
                            (t.deadline_at IS NULL), t.deadline_at ASC,
                            (t.assigned_date IS NULL), t.assigned_date ASC, t.assigned_window ASC,
                            COALESCE(c.priority_tier, t.priority, 2) ASC, t.id ASC
                   LIMIT ?""", (limit,))


def today_plan(date: Optional[str] = None) -> List[Dict[str, Any]]:
    d = date or now_pkt().strftime("%Y-%m-%d")
    return rows("""SELECT p.*, t.title, t.course_code, t.est_mins, t.status AS task_status FROM plan_items p
                   LEFT JOIN tasks t ON t.id=p.task_id
                   WHERE p.date=? AND p.status IN ('planned','partial') ORDER BY p.start_time""", (d,))


def add_plan_item(task_id: int, date: str, start_time: str, end_time: str, block_id: Optional[int] = None,
                  location: Optional[str] = None, explain: Any = None) -> int:
    now = iso_utc()
    with tx() as c:
        prev = c.execute("""SELECT id,version FROM plan_items WHERE task_id=? AND status='planned'
                            ORDER BY version DESC LIMIT 1""", (task_id,)).fetchone()
        cur = c.execute("""INSERT INTO plan_items (task_id,block_id,date,start_time,end_time,location,version,
                           explain_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (task_id, block_id, date, start_time, end_time, location,
                         (prev["version"] + 1) if prev else 1, json.dumps(explain) if explain else None, now))
        pid = cur.lastrowid
        if prev:
            c.execute("UPDATE plan_items SET status='moved', superseded_by=? WHERE id=?", (pid, prev["id"]))
        c.execute("UPDATE tasks SET status='scheduled', assigned_date=?, assigned_day=?, assigned_window=?, "
                  "assigned_location=?, needs_slot=0, updated_at=? WHERE id=?",
                  (date, parse_ts(date).strftime("%A") if parse_ts(date) else None,
                   f"{start_time}-{end_time}", location, now, task_id))
    audit("plan_item", "plan_items", pid, None, {"task_id": task_id, "date": date, "window": f"{start_time}-{end_time}"})
    return pid


def open_commitments(limit: int = 20) -> List[Dict[str, Any]]:
    return rows("""SELECT c.*, t.title FROM commitments c LEFT JOIN tasks t ON t.id=c.task_id
                   WHERE c.status='open' ORDER BY (c.due_at IS NULL), c.due_at LIMIT ?""", (limit,))


def record_commitment(promise: str, domain: str = "Academics", task_id: Optional[int] = None,
                      plan_item_id: Optional[int] = None, due_at: Optional[str] = None,
                      ladder_level: Optional[int] = None, turn_id: Optional[int] = None) -> int:
    lvl = ladder_level or int(re.sub(r"\D", "", str(ladder_for(domain))) or 1)
    cid = write("""INSERT INTO commitments (domain,promise,task_id,plan_item_id,made_at,due_at,ladder_level,turn_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (domain, promise, task_id, plan_item_id, iso_utc(),
                 iso_utc(resolve_deadline(due_at)) if due_at else None, lvl, turn_id))
    audit("commitment", "commitments", cid, None, {"promise": promise, "due_at": due_at})
    return cid


def record_outcome(result: str, commitment_id: Optional[int] = None, plan_item_id: Optional[int] = None,
                   task_id: Optional[int] = None, reason: Optional[str] = None,
                   actual_mins: Optional[int] = None, turn_id: Optional[int] = None) -> Dict[str, Any]:
    """Closes the loop: outcome -> commitment status -> task status -> estimation error (P6)."""
    est = scalar("SELECT est_mins FROM tasks WHERE id=?", (task_id,)) if task_id else None
    err = (actual_mins - est) if (actual_mins is not None and est) else None
    now = iso_utc()
    with tx() as c:
        cur = c.execute("""INSERT INTO outcomes (commitment_id,plan_item_id,task_id,result,reason,actual_mins,
                           estimate_error_mins,recorded_at,turn_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (commitment_id, plan_item_id, task_id, result, reason, actual_mins, err, now, turn_id))
        oid = cur.lastrowid
        if commitment_id:
            c.execute("UPDATE commitments SET status=?, closed_at=? WHERE id=?",
                      ("kept" if result == "done" else ("renegotiated" if result == "partial" else "broken"),
                       now, commitment_id))
        if plan_item_id:
            c.execute("UPDATE plan_items SET status=? WHERE id=?",
                      (result if result in ("done", "partial", "missed") else "done", plan_item_id))
        if task_id:
            c.execute("UPDATE tasks SET status=?, completed_at=?, updated_at=? WHERE id=?",
                      ("completed" if result == "done" else ("missed" if result == "missed" else "in_progress"),
                       now if result == "done" else None, now, task_id))
    audit("outcome", "outcomes", oid, None, {"result": result, "reason": reason, "estimate_error_mins": err}, turn_id=turn_id)
    return {"outcome_id": oid, "estimate_error_mins": err, "result": result}


# ============================== packs / academics ============================
def upsert_syllabus_item(course_code: str, chapter_topic: str, taught_scope: str, personal_target: str,
                         deadline_hint: Optional[str] = None, status: str = "not_started") -> int:
    """B4: no duplicate syllabus rows for the same taught scope."""
    now = iso_utc()
    dl = resolve_deadline(deadline_hint)
    with tx() as c:
        found = c.execute("""SELECT id FROM syllabus_progress WHERE course_code=? AND chapter_topic=?
                             AND taught_scope=?""", (course_code.upper(), chapter_topic, taught_scope)).fetchone()
        if found:
            c.execute("""UPDATE syllabus_progress SET personal_target=?, self_study_status=?, deadline_label=?,
                         deadline_at=?, updated_at=? WHERE id=?""",
                      (personal_target, status, deadline_hint, iso_utc(dl) if dl else None, now, found["id"]))
            return found["id"]
        cur = c.execute("""INSERT INTO syllabus_progress (course_code,chapter_topic,taught_scope,personal_target,
                           self_study_status,deadline_label,deadline_at,updated_at,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (course_code.upper(), chapter_topic, taught_scope, personal_target, status,
                         deadline_hint or "Flexible", iso_utc(dl) if dl else None,
                         now_pkt().strftime("%Y-%m-%d %H:%M %Z"), now))
        return cur.lastrowid


def log_lecture_debrief(course_code: str, topic: str, taught_scope: Optional[str] = None,
                        confusion_points: Optional[str] = None, instructor_hints: Optional[str] = None,
                        extra: Any = None, session_id: Optional[int] = None) -> int:
    """B6/B10: lecture_logs finally has a writer (and a voice tool can call this)."""
    lid = write("""INSERT INTO lecture_logs (course_code,topic,taught_scope,confusion_points,instructor_hints,
                   debrief_json,logged_at,session_id) VALUES (?,?,?,?,?,?,?,?)""",
                (course_code.upper(), topic, taught_scope, confusion_points, instructor_hints,
                 json.dumps(extra) if extra else None, iso_utc(), session_id))
    audit("lecture_debrief", "lecture_logs", lid)
    return lid


def log_study_session(course_code: str, topic: str, method_used: Optional[str] = None,
                      duration_mins: Optional[int] = None, retention_confidence: int = 3,
                      plan_item_id: Optional[int] = None, outcome: str = "done",
                      notes: Optional[str] = None) -> int:
    now = iso_utc()
    sid = write("""INSERT INTO study_sessions (timestamp,course_code,topic,method_used,duration_mins,
                   retention_confidence,started_at,ended_at,plan_item_id,outcome,notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (now_pkt().strftime("%Y-%m-%d %H:%M %Z"), course_code.upper(), topic, method_used,
                 duration_mins, retention_confidence,
                 iso_utc(utc_now() - timedelta(minutes=duration_mins or 0)), now, plan_item_id, outcome, notes))
    if duration_mins:
        set_signal("last_study_mins", duration_mins, ttl_minutes=720, source="observed")
    audit("study_session", "study_sessions", sid)
    return sid


def upsert_spaced_review(course_code: str, item_key: str, topic: Optional[str] = None,
                         quality: int = 3) -> Dict[str, Any]:
    """B10: one row per item (no per-session duplicates), SM-2 style scheduling, surfaced by due list."""
    r = row("SELECT * FROM spaced_reviews WHERE course_code=? AND item_key=?", (course_code.upper(), item_key))
    now = utc_now()
    if r:
        ease = max(1.3, float(r["ease"]) + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        reps = int(r["reps"]) + 1
        interval = 1 if reps == 1 else (6 if reps == 2 else round(float(r["interval_days"]) * ease))
        rid = r["id"]
        write("""UPDATE spaced_reviews SET ease=?,interval_days=?,reps=?,due_at=?,last_reviewed_at=?,
                 topic=COALESCE(?,topic) WHERE id=?""",
              (ease, interval, reps, iso_utc(now + timedelta(days=interval)), iso_utc(now), topic, rid))
    else:
        interval = 1
        rid = write("""INSERT INTO spaced_reviews (course_code,item_key,topic,due_at,last_reviewed_at,reps)
                       VALUES (?,?,?,?,?,1)""",
                    (course_code.upper(), item_key, topic, iso_utc(now + timedelta(days=1)), iso_utc(now)))
    return {"review_id": rid, "interval_days": interval, "due_at": iso_utc(now + timedelta(days=interval))}


def due_spaced_reviews(limit: int = 10) -> List[Dict[str, Any]]:
    return rows("SELECT * FROM spaced_reviews WHERE due_at<=? ORDER BY due_at LIMIT ?", (iso_utc(), limit))


def log_sandbox_event(kind: str, payload: Any = None, result: Optional[str] = None,
                      course_code: Optional[str] = None) -> int:
    return write("INSERT INTO sandbox_logs (ts_utc,kind,course_code,payload_json,result) VALUES (?,?,?,?,?)",
                 (iso_utc(), kind, course_code, json.dumps(payload) if payload else None, result))


def record_assessment(course_code: str, label: str, type_: str, max_marks: float,
                      obtained: float = 0, expected: float = 0, status: str = "Upcoming",
                      due_hint: Optional[str] = None, turn_id: Optional[int] = None) -> int:
    """G3: marks are rows; changing one changes the next prompt with no code edit."""
    dl = resolve_deadline(due_hint)
    aid = write("""INSERT INTO assessments (course_code,label,type,max_marks,obtained,expected,status,
                   due_at,recorded_at,turn_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (course_code.upper(), label, type_, max_marks, obtained, expected, status,
                 iso_utc(dl) if dl else None, iso_utc(), turn_id))
    audit("assessment", "assessments", aid, None,
          {"course": course_code, "label": label, "obtained": obtained, "max": max_marks}, turn_id=turn_id)
    return aid


# ============================== proactive / triggers.py ======================
def ladder_for(domain: str) -> str:
    key = "accountability_academics" if domain.lower() == "academics" else "accountability_other"
    return str(get_config(key, "L1"))


def in_quiet_hours(domain: str = "*", at: Optional[datetime] = None) -> bool:
    at = (at or now_pkt()).astimezone(PKT)
    hhmm = at.strftime("%H:%M")
    for q in rows("SELECT * FROM quiet_hours WHERE enabled=1 AND domain IN (?,'*')", (domain,)):
        s, e = q["start_time"], q["end_time"]
        overnight = s > e                      # e.g. 23:00 -> 07:00
        if (hhmm >= s or hhmm < e) if overnight else (s <= hhmm < e):
            return True
    return False


def nudges_sent_today() -> int:
    start = iso_utc(now_pkt().replace(hour=0, minute=0, second=0, microsecond=0))
    return int(scalar("SELECT COUNT(*) FROM nudges WHERE sent_at>=? AND status IN ('sent','seen','acted')",
                      (start,), 0))


def nudges_planned_today() -> int:
    """Queued + already sent, so the cap binds on the queue too, not just on delivery."""
    start = iso_utc(now_pkt().replace(hour=0, minute=0, second=0, microsecond=0))
    return int(scalar("""SELECT COUNT(*) FROM nudges WHERE scheduled_at>=?
                         AND status IN ('pending','sent','seen','acted')""", (start,), 0))


def nudge_budget_left() -> int:
    return max(0, int(get_config("nag_budget", 5)) - nudges_planned_today())


def create_nudge(trigger_name: str, payload: Any = None, when: Optional[datetime] = None,
                 task_id: Optional[int] = None, commitment_id: Optional[int] = None,
                 channel: str = "hud", reason: Optional[str] = None) -> Optional[int]:
    """Quiet hours and the daily cap are enforced here, not in the model."""
    trg = row("SELECT * FROM triggers WHERE name=? AND enabled=1", (trigger_name,))
    if not trg:
        return None
    when = when or utc_now()
    dom = trg["domain"] if trg["domain"] != "*" else "Academics"
    if in_quiet_hours(dom, when.astimezone(PKT)):
        return write("""INSERT INTO nudges (trigger_id,task_id,commitment_id,scheduled_at,status,payload_json,reason)
                        VALUES (?,?,?,?,'suppressed',?,?)""",
                     (trg["id"], task_id, commitment_id, iso_utc(when),
                      json.dumps(payload) if payload else None, "quiet_hours"))
    if nudge_budget_left() <= 0:
        return write("""INSERT INTO nudges (trigger_id,task_id,commitment_id,scheduled_at,status,payload_json,reason)
                        VALUES (?,?,?,?,'suppressed',?,?)""",
                     (trg["id"], task_id, commitment_id, iso_utc(when),
                      json.dumps(payload) if payload else None, "nag_budget"))
    ch = row("SELECT id FROM channels WHERE name=?", (channel,))
    return write("""INSERT INTO nudges (trigger_id,task_id,commitment_id,channel_id,scheduled_at,payload_json,reason)
                    VALUES (?,?,?,?,?,?,?)""",
                 (trg["id"], task_id, commitment_id, ch["id"] if ch else None, iso_utc(when),
                  json.dumps(payload) if payload else None, reason))


def due_nudges(limit: int = 10) -> List[Dict[str, Any]]:
    return rows("""SELECT n.*, t.name AS trigger_name, t.kind, t.ladder_level FROM nudges n
                   LEFT JOIN triggers t ON t.id=n.trigger_id
                   WHERE n.status='pending' AND n.scheduled_at<=? ORDER BY n.scheduled_at LIMIT ?""",
                (iso_utc(), limit))


def mark_nudge(nudge_id: int, status: str, snooze_mins: Optional[int] = None) -> None:
    now = iso_utc()
    if status == "sent":
        start = iso_utc(now_pkt().replace(hour=0, minute=0, second=0, microsecond=0))
        delivered = int(scalar("""SELECT COUNT(*) FROM nudges WHERE sent_at>=? AND id<>?
                                  AND status IN ('sent','seen','acted')""", (start, nudge_id), 0))
        if delivered >= int(get_config("nag_budget", 5)) or in_quiet_hours():
            status = "suppressed"      # re-check at delivery: budget/quiet hours may have changed
    col = {"sent": "sent_at", "seen": "seen_at", "acted": "acted_at"}.get(status)
    with tx() as c:
        c.execute("UPDATE nudges SET status=? WHERE id=?", (status, nudge_id))
        if col:
            c.execute(f"UPDATE nudges SET {col}=? WHERE id=?", (now, nudge_id))
        if status == "snoozed":
            c.execute("UPDATE nudges SET snoozed_until=?, scheduled_at=? WHERE id=?",
                      (iso_utc(utc_now() + timedelta(minutes=snooze_mins or 30)),
                       iso_utc(utc_now() + timedelta(minutes=snooze_mins or 30)), nudge_id))
    audit("nudge_" + status, "nudges", nudge_id)


# ============================== learning / params.py =========================
def propose_param(key: str, value: float, domain: str = "general", evidence: Any = None,
                  confidence: float = 0.5) -> int:
    pid = write("""INSERT INTO learned_params (key,domain,value,prev_value,confidence,evidence_json,status)
                   VALUES (?,?,?,?,?,?,'proposed')""",
                (key, domain, value, get_param(key, domain), confidence,
                 json.dumps(evidence) if evidence else None))
    write("""INSERT INTO proposals (kind,payload_json,evidence_json,created_at)
             VALUES ('param',?,?,?)""",
          (json.dumps({"param_id": pid, "key": key, "value": value}),
           json.dumps(evidence) if evidence else None, iso_utc()))
    return pid


def apply_param(param_id: int, actor: str = "haider") -> None:
    write("UPDATE learned_params SET status='active', applied_at=? WHERE id=?", (iso_utc(), param_id))
    audit("apply_param", "learned_params", param_id, actor=actor)


def rollback_param(param_id: int, actor: str = "haider") -> None:
    write("UPDATE learned_params SET status='rolled_back', rolled_back_at=? WHERE id=?", (iso_utc(), param_id))
    audit("rollback_param", "learned_params", param_id, actor=actor)


def get_param(key: str, domain: str = "general", default: Optional[float] = None) -> Optional[float]:
    r = row("""SELECT value,confidence,applied_at,decay_halflife_days FROM learned_params
               WHERE key=? AND domain=? AND status='active' ORDER BY applied_at DESC LIMIT 1""", (key, domain))
    if not r:
        return default
    age = minutes_since(r["applied_at"])
    if age and r["decay_halflife_days"]:
        half = float(r["decay_halflife_days"]) * 1440
        conf = float(r["confidence"]) * (0.5 ** (age / half))
        if conf < 0.2:
            return default  # decayed below trust threshold
    return float(r["value"])


def weekly_review_payload(days: int = 7) -> Dict[str, Any]:
    since = iso_utc(utc_now() - timedelta(days=days))
    agg = row("""SELECT COUNT(*) AS n, SUM(result='done') AS done, SUM(result='partial') AS partial,
                 SUM(result='missed') AS missed, AVG(estimate_error_mins) AS err
                 FROM outcomes WHERE recorded_at>=?""", (since,)) or {}
    n = agg.get("n") or 0
    adherence = round(((agg.get("done") or 0) + 0.5 * (agg.get("partial") or 0)) / n, 3) if n else None
    return {"period_start": since, "period_end": iso_utc(), "outcomes": agg,
            "adherence": adherence, "adherence_rule": "(done + 0.5*partial) / total",
            "estimation_error_mins": round(agg["err"], 1) if agg.get("err") is not None else None,
            "open_commitments": open_commitments(),
            "params_changed": rows("SELECT * FROM learned_params WHERE applied_at>=?", (since,)),
            "top_reasons": rows("""SELECT reason, COUNT(*) n FROM outcomes WHERE recorded_at>=? AND reason IS NOT NULL
                                   GROUP BY reason ORDER BY n DESC LIMIT 5""", (since,))}


def save_review(kind: str, payload: Dict[str, Any]) -> int:
    rid = write("""INSERT INTO reviews (kind,period_start,period_end,adherence,estimation_error,body_json,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (kind, payload.get("period_start"), payload.get("period_end"), payload.get("adherence"),
                 payload.get("estimation_error_mins"), json.dumps(payload), iso_utc()))
    audit("review", "reviews", rid, after={"kind": kind})
    return rid


# ============================== live / manifest.py (data side) ===============
def register_tool_spec(name: str, description: str, params: Optional[Dict] = None, handler: str = "",
                       risk: str = "read", confirmation: Optional[str] = None, behaviour: str = "BLOCKING",
                       scheduling: str = "WHEN_IDLE", domain: str = "*") -> None:
    conf = confirmation or ("always" if risk == "destructive" else "never")
    write("""INSERT INTO tool_specs (name,description,params_json,handler,risk,confirmation,behaviour,
             scheduling,domain,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(name) DO UPDATE SET description=excluded.description, params_json=excluded.params_json,
             handler=excluded.handler, risk=excluded.risk, confirmation=excluded.confirmation,
             behaviour=excluded.behaviour, scheduling=excluded.scheduling, version=tool_specs.version+1""",
          (name, description, json.dumps(params or {"type": "object", "properties": {}}), handler, risk,
           conf, behaviour, scheduling, domain, iso_utc()))


def tool_declarations(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """Gemini declarations generated from specs at connect (§7.1)."""
    specs = rows("SELECT * FROM tool_specs WHERE enabled=1" + (" AND domain IN (?,'*')" if domain else ""),
                 (domain,) if domain else ())
    return [{"name": s["name"], "description": s["description"], "parameters": json.loads(s["params_json"])}
            for s in specs]


def set_capability(key: str, value: Any, verified: bool = True) -> None:
    write("""INSERT INTO capabilities (key,value_json,verified,probed_at) VALUES (?,?,?,?)
             ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, verified=excluded.verified,
             probed_at=excluded.probed_at""", (key, json.dumps(value), int(verified), iso_utc()))


def capability_manifest() -> Dict[str, Any]:
    """G7: Jarvis can describe itself from data instead of improvising."""
    return {"schema_version": schema_version(),
            "tools": rows("SELECT name,risk,confirmation,behaviour,scheduling,domain FROM tool_specs WHERE enabled=1"),
            "domains": rows("SELECT name,enabled,pack FROM domains"),
            "probed": rows("SELECT * FROM capabilities"),
            "limits": {k: get_config(k) for k in ("live_minute_cap", "nag_budget", "rollover_cap",
                                                  "quiet_start", "quiet_end", "fact_reask_cap")},
            "policies": rows("SELECT * FROM freshness_policy")}


def get_prompt_context(fact_budget: int = 25, turn_budget: int = 6) -> Dict[str, Any]:
    """P1/§5 read path inputs: identity core, today's plan, commitments, manifest, top facts,
    last summary, a few raw turns. The assembler file will token-budget these."""
    dom_rows = rows("SELECT name FROM domains WHERE enabled=1")
    facts = get_facts(include_private=True, limit=fact_budget)
    return {"now_utc": iso_utc(), "now_pkt": fmt_pkt(iso_utc()),
            "profile": row("SELECT * FROM profile WHERE id=1") or {},
            "config": all_config(), "domains": [d["name"] for d in dom_rows],
            "working_state": get_working_state(), "fresh_signals": get_signals(),
            "stale_signals": stale_signals(),
            "today_blocks": rows("SELECT * FROM time_blocks WHERE day_of_week=? AND active=1 ORDER BY start_time",
                                 (now_pkt().strftime("%A"),)),
            "today_plan": today_plan(), "tasks": tasks_for_prompt(),
            "open_commitments": open_commitments(), "facts": facts,
            "facts_due": facts_due_for_review(int(get_config("fact_reask_cap", 3))),
            "rules": get_rules(), "last_summary": last_session_summary(),
            "recent_turns": recent_turns(turn_budget), "manifest": capability_manifest(),
            "due_reviews": due_spaced_reviews()}


# ============================== ops / migrations & maintenance ===============
def normalise_timestamps() -> Dict[str, int]:
    """B9: bring any legacy-format column to UTC ISO-8601. Idempotent."""
    targets = [("tasks", "id", "created_at"), ("syllabus_progress", "id", "updated_at"),
               ("study_sessions", "id", "timestamp"), ("working_state", "id", "updated_at_utc"),
               ("goals", "id", "created_at"), ("projects", "id", "created_at"),
               ("people", "id", "created_at"), ("profile", "id", "created_at")]
    counts: Dict[str, int] = {}
    with tx() as c:
        for table, pk, col in targets:
            try:
                rs = c.execute(f"SELECT {pk} AS pk, {col} AS v FROM {table} WHERE {col} IS NOT NULL").fetchall()
            except sqlite3.OperationalError:
                continue
            n = 0
            for r in rs:
                v = str(r["v"])
                if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$", v):
                    continue
                dt = parse_ts(v)
                if dt:
                    c.execute(f"UPDATE {table} SET {col}=? WHERE {pk}=?", (iso_utc(dt), r["pk"]))
                    n += 1
            if n:
                counts[table] = n
        # B5 backfill: raw deadline words -> canonical deadline_at
        n = 0
        for r in c.execute("SELECT id,due_date FROM tasks WHERE deadline_at IS NULL AND due_date IS NOT NULL").fetchall():
            dl = resolve_deadline(r["due_date"])
            if dl:
                c.execute("UPDATE tasks SET deadline_at=? WHERE id=?", (iso_utc(dl), r["id"]))
                n += 1
        if n:
            counts["tasks.deadline_at"] = n
        # B4 backfill flag: unslotted pending tasks are marked, never silently dropped
        n = c.execute("""UPDATE tasks SET needs_slot=1 WHERE status IN ('pending','scheduled')
                         AND (assigned_date IS NULL OR assigned_window IS NULL) AND needs_slot=0""").rowcount
        if n:
            counts["tasks.needs_slot"] = n
    if counts:
        audit("normalise_timestamps", "db", after=counts)
    return counts


def dedupe_tasks() -> int:
    """B4: same course+title pending twice -> keep the earliest, mark the rest 'duplicate'."""
    dupes = rows("""SELECT id FROM tasks WHERE status IN ('pending','scheduled') AND id NOT IN
                    (SELECT MIN(id) FROM tasks WHERE status IN ('pending','scheduled')
                     GROUP BY COALESCE(course_code,''), LOWER(TRIM(title)))""")
    for d in dupes:
        write("UPDATE tasks SET status='duplicate', updated_at=? WHERE id=?", (iso_utc(), d["id"]))
        audit("dedupe_task", "tasks", d["id"], after={"status": "duplicate"})
    return len(dupes)


def dedupe_syllabus() -> int:
    dupes = rows("""SELECT id FROM syllabus_progress WHERE id NOT IN
                    (SELECT MIN(id) FROM syllabus_progress GROUP BY course_code, chapter_topic, taught_scope)""")
    for d in dupes:
        write("DELETE FROM syllabus_progress WHERE id=?", (d["id"],))
    return len(dupes)


def migrate_legacy_v3(path: Optional[str] = None, actor: str = "migration",
                      force: bool = False) -> Dict[str, Any]:
    """M0 step 2: backup, import, backfill, dedupe, normalise. No-op if no legacy DB is found
    or if this source was already imported (pass force=True to re-import deliberately).
    Handles the v3 academic schema (fixed quiz/assignment/mid columns) and the wizard-era butler.db."""
    candidates = [Path(path)] if path else [DB_PATH.parent / "jarvis_academic.db",
                                            DB_PATH.parent / "butler_v3.db",
                                            DB_PATH.parent / "butler.db.v3"]
    src = next((p for p in candidates if p and p.exists()), None)
    if not src:
        return {"migrated": False, "reason": "no legacy database found"}
    fingerprint = f"{src.name}:{src.stat().st_size}:{int(src.stat().st_mtime)}"
    already = row("SELECT value_json FROM capabilities WHERE key='legacy_migration'")
    if already and not force and fingerprint in json.loads(already["value_json"]).get("sources", []):
        return {"migrated": False, "reason": "already imported", "source": str(src)}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    shutil.copy2(src, BACKUP_DIR / f"{src.name}.{stamp}.bak")
    if DB_PATH.exists():
        backup("pre_migration")
    old = sqlite3.connect(src)
    old.row_factory = DictRow
    have = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    report: Dict[str, Any] = {"migrated": True, "source": str(src), "imported": {}, "unmapped_columns": {}}
    salvage: List[tuple] = []

    def cols(t: str) -> List[str]:
        return [r[1] for r in old.execute(f"PRAGMA table_info({t})")] if t in have else []

    def bump(k: str, n: int = 1) -> None:
        report["imported"][k] = report["imported"].get(k, 0) + n

    with tx() as c:
        if "courses" in have:
            cc = cols("courses")
            for r in old.execute("SELECT * FROM courses"):
                c.execute("""INSERT INTO courses (code,name,instructor,credits,nature,priority_tier,
                             time_share_pct,difficulty,study_protocol,risk_flags,updated_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO NOTHING""",
                          (str(r.get("code", "")).upper(), r.get("name") or r.get("code"), r.get("instructor"),
                           r.get("credits", 3), r.get("nature", "Mixed"), r.get("priority_tier", 2),
                           r.get("time_share_pct", 10.0), r.get("difficulty", 3), r.get("study_protocol"),
                           r.get("risk_flags"), iso_utc()))
                bump("courses")
                # v3 fixed columns -> assessment rows (G6). Match quiz1 / quiz_1 / quiz1_marks alike.
                consumed = {"code", "name", "instructor", "credits", "nature", "priority_tier",
                            "time_share_pct", "difficulty", "study_protocol", "risk_flags"}
                for label, typ, mx in (("Quiz 1", "Quiz", 2.5), ("Quiz 2", "Quiz", 2.5),
                                       ("Assignment", "Assignment", 5), ("Midterm", "Midterm", 25),
                                       ("Final", "Final", 50)):
                    want = label.lower().replace(" ", "")
                    field = next((f for f in cc if want in f.lower().replace("_", "")), None)
                    if field is None and want == "midterm":
                        field = next((f for f in cc if f.lower().startswith("mid")), None)
                    if field:
                        consumed.add(field)
                        val = float(r.get(field) or 0)
                        c.execute("""INSERT INTO assessments (course_code,label,type,max_marks,obtained,status,recorded_at)
                                     VALUES (?,?,?,?,?,?,?)""",
                                  (str(r.get("code", "")).upper(), label, typ, mx, min(val, mx),
                                   "Completed" if val else "Upcoming", iso_utc()))
                        bump("assessments")
                left = [f for f in cc if f not in consumed]
                if left:
                    report["unmapped_columns"]["courses"] = left
                    for f in left:
                        if r.get(f) is not None:
                            salvage.append((f"academics.{str(r.get('code','')).upper()}", f, str(r.get(f))))
        if "assessments" in have:
            for r in old.execute("SELECT * FROM assessments"):
                c.execute("""INSERT INTO assessments (course_code,label,type,max_marks,obtained,expected,status,recorded_at)
                             VALUES (?,?,?,?,?,?,?,?)""",
                          (str(r.get("course_code", "")).upper(), r.get("label"), r.get("type") or "Other",
                           float(r.get("max_marks") or 0), float(r.get("obtained") or 0),
                           float(r.get("expected") or 0), r.get("status") or "Upcoming", iso_utc()))
                bump("assessments")
        if "tasks" in have:
            for r in old.execute("SELECT * FROM tasks"):
                dl = resolve_deadline(r.get("due_date") or r.get("deadline"))
                slotted = bool(r.get("assigned_date") and r.get("assigned_window"))
                c.execute("""INSERT INTO tasks (course_code,title,est_mins,status,is_surprise_quiz,due_date,
                             deadline_at,assigned_day,assigned_date,assigned_window,assigned_location,
                             created_at,needs_slot,rollover_count)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (r.get("course_code"), r.get("title") or "Untitled", int(r.get("est_mins") or 45),
                           (r.get("status") or "pending"),
                           0,  # B3: never infer surprise-quiz from a deadline
                           r.get("due_date"), iso_utc(dl) if dl else None, r.get("assigned_day"),
                           r.get("assigned_date"), r.get("assigned_window"), r.get("assigned_location"),
                           iso_utc(parse_ts(r.get("created_at")) or utc_now()), 0 if slotted else 1,
                           int(r.get("rollover_count") or 0)))
                bump("tasks")
        for tbl, sql in (
            ("time_blocks", """INSERT INTO time_blocks (day_of_week,start_time,end_time,type,location,course_focus,guardrail)
                               VALUES (?,?,?,?,?,?,?)"""),
            ("syllabus_progress", """INSERT INTO syllabus_progress (course_code,chapter_topic,taught_scope,personal_target,
                                     self_study_status,deadline_label,updated_at) VALUES (?,?,?,?,?,?,?)"""),
            ("goals", "INSERT INTO goals (domain,title,why,metric,deadline,priority) VALUES (?,?,?,?,?,?)"),
            ("projects", "INSERT INTO projects (name,description,deadline,status) VALUES (?,?,?,?)"),
            ("people", "INSERT INTO people (name,relation,notes) VALUES (?,?,?)"),
        ):
            if tbl not in have:
                continue
            fields = [f.strip() for f in sql.split("(")[1].split(")")[0].split(",")]
            available = cols(tbl)
            for r in old.execute(f"SELECT * FROM {tbl}"):
                vals = [r.get(f) if f in available else None for f in fields]
                if tbl == "syllabus_progress":
                    vals[4] = vals[4] or "not_started"
                    vals[6] = iso_utc(parse_ts(vals[6]) or utc_now())
                if tbl == "time_blocks" and not (vals[0] and vals[1] and vals[2]):
                    continue
                c.execute(sql, tuple(vals))
                bump(tbl)
        if "episodic_memory" in have:
            sid = c.execute("SELECT id FROM sessions WHERE session_key='legacy'").fetchone()[0]
            for r in old.execute("SELECT * FROM episodic_memory ORDER BY id"):
                content = (r.get("content") or "").strip()
                if not content:
                    continue
                role = r.get("role") if r.get("role") in ("user", "jarvis", "tool", "system") else "system"
                c.execute("INSERT INTO turns (session_id,role,content,ts_utc) VALUES (?,?,?,?)",
                          (sid, role, content, iso_utc(parse_ts(r.get("timestamp_pkt"), naive_zone=PKT) or utc_now())))
                bump("turns")
        if "checkins" in have:
            for r in old.execute("SELECT * FROM checkins"):
                c.execute("""INSERT INTO checkins (ts_utc,location,energy,hydration,fed,stress,activity,notes,
                             resulting_focus) VALUES (?,?,?,?,?,?,?,?,?)""",
                          (iso_utc(parse_ts(r.get("timestamp") or r.get("ts_utc") or r.get("timestamp_pkt")) or utc_now()),
                           r.get("location"), r.get("energy"), r.get("hydration"), r.get("fed"), r.get("stress"),
                           r.get("current_activity") or r.get("activity"), r.get("notes"), r.get("resulting_focus")))
                bump("checkins")
        if "working_state" in have:
            r = old.execute("SELECT * FROM working_state LIMIT 1").fetchone()
            if r:
                c.execute("""UPDATE working_state SET location=?,energy=?,hydration=?,fed=?,stress=?,
                             current_activity=?,updated_at_utc=? WHERE id=1""",
                          (r.get("location", "Home"), int(r.get("energy") or 3), int(r.get("hydration") or 3),
                           int(r.get("fed") or 1), int(r.get("stress") or 2), r.get("current_activity", "Ready"),
                           iso_utc(parse_ts(r.get("updated_at"), naive_zone=PKT) or utc_now())))
                bump("working_state")
    old.close()
    for dom, key, val in salvage:      # nothing from v3 disappears: unmatched values become facts
        remember(dom, key, val, source="observed", evidence=f"imported from {src.name}", importance=2)
    report["salvaged_as_facts"] = len(salvage)
    report["deduped_tasks"] = dedupe_tasks()
    report["deduped_syllabus"] = dedupe_syllabus()
    report["normalised"] = normalise_timestamps()
    prev = json.loads(already["value_json"]) if already else {"sources": []}
    prev["sources"] = list(dict.fromkeys(prev.get("sources", []) + [fingerprint]))
    prev["last_report"] = report
    set_capability("legacy_migration", prev, verified=True)
    audit("migrate_legacy_v3", "db", after=report, actor=actor)
    return report


def backup(label: str = "nightly", keep: int = 14) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"butler_{label}_{utc_now().strftime('%Y%m%dT%H%M%S')}.db"
    with tx() as src:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    files = sorted(BACKUP_DIR.glob("butler_*.db"))
    for f in files[:-keep]:
        f.unlink(missing_ok=True)
    audit("backup", "db", after={"path": str(dest)})
    return str(dest)


def integrity_check() -> Dict[str, Any]:
    with tx() as c:
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        fk = [dict(r) for r in c.execute("PRAGMA foreign_key_check").fetchall()]
    return {"integrity": ok, "foreign_key_violations": fk}


def run_maintenance() -> Dict[str, Any]:
    """Nightly: prune raw turns, dedupe, normalise, decay, backup (§5 consolidation + ops)."""
    return {"pruned_turns": prune_raw_turns(), "deduped_tasks": dedupe_tasks(),
            "deduped_syllabus": dedupe_syllabus(), "normalised": normalise_timestamps(),
            "facts_due": len(facts_due_for_review(100)), "stale_signals": [s["key"] for s in stale_signals()],
            "backup": backup(), "integrity": integrity_check()}


def stats() -> Dict[str, Any]:
    with tx() as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    return {"schema_version": schema_version(), "tables": len(tables), "row_counts": counts,
            "setup_complete": is_setup_complete(), "db_path": str(DB_PATH)}


if __name__ == "__main__":
    init_db()
    print(f"schema v{schema_version()} · setup_complete={is_setup_complete()} · integrity={integrity_check()['integrity']}")
    remember("identity", "semester", "5th", evidence="stated at first boot")
    remember("identity", "semester", "6th", source="inferred", evidence="course list suggests 6th")
    print("facts:", [(x["key"], x["value"], x["status"]) for x in get_facts("identity")],
          "| proposed:", [(x["key"], x["value"]) for x in get_facts("identity", status="proposed")])
    sid = start_session("selftest")
    for i in range(60):
        append_turn(sid, "user" if i % 2 else "jarvis", f"turn {i}")
    print("turns kept:", scalar("SELECT COUNT(*) FROM turns"), "(no 40-row cap)")
    print("deadline parser:", {h: str(resolve_deadline(h)) for h in ("today", "tomorrow 9am", "Nov 18", "next week")})
    print("quiet hours now:", in_quiet_hours(), "| nudge budget left:", nudge_budget_left())
    s = stats()
    print(f"{s['tables']} tables · non-empty: {sum(1 for v in s['row_counts'].values() if v > 0)}")
    print("awaiting first write:", [t for t, n in s["row_counts"].items() if n == 0 and not t.startswith(("facts_fts", "turns_fts", "journal_fts"))])
