"""
JARVIS — Milestone 0 Migration ("The Safety Net")
==================================================
What this does, in plain English:
  1. Makes a complete backup copy of your database FIRST.
  2. Prints a health report of what it finds.
  3. Repairs known data issues:
       - marks duplicate task tickets as removed (keeps the best copy)
       - gives missing day/time/location to un-slotted tasks where possible
       - normalises mixed timestamp formats
       - clears false "surprise quiz" flags stamped by the old code
       - adds a version tag and speed indexes to the database
  4. Saves a report file called migration_report.txt

How to run:   python migrate_v3_to_v4.py
Safe to run more than once. If anything fails, nothing is lost.
"""

import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "jarvis_academic.db"
PKT = timezone(timedelta(hours=5))  # Lahore = UTC+5, no daylight saving
REPORT = []


def say(msg=""):
    print(msg)
    REPORT.append(msg)


# ---------- timestamp repair ----------

TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?\s*(PKT|UTC)?\s*$"
)
ALREADY_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?\+05:00$")


def normalize_timestamp(raw):
    """Returns (new_value, was_changed). Unknown formats are left untouched."""
    if raw is None:
        return None, False
    raw = str(raw).strip()
    if not raw or ALREADY_ISO.match(raw):
        return raw, False
    m = TS_RE.match(raw)
    if not m:
        return raw, False  # e.g. a word like "Wednesday" - not a timestamp
    date_part, hhmm, ss, zone = m.groups()
    try:
        dt = datetime.strptime(f"{date_part} {hhmm}:{ss or '00'}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return raw, False
    if zone == "PKT":
        dt = dt.replace(tzinfo=PKT)
    else:
        # Labeled UTC, or no label at all (old code stored UTC silently).
        # Convert to Lahore time.
        dt = dt.replace(tzinfo=timezone.utc).astimezone(PKT)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+05:00"), True


def fix_timestamps(conn):
    changed = 0
    shown = 0
    targets = [
        ("episodic_memory", "timestamp_pkt"),
        ("checkins", "timestamp"),
        ("tasks", "created_at"),
        ("syllabus_progress", "updated_at"),
    ]
    for table, col in targets:
        rows = conn.execute(f"SELECT rowid AS rid, {col} AS ts FROM {table}").fetchall()
        for r in rows:
            new_ts, was_changed = normalize_timestamp(r["ts"])
            if was_changed:
                conn.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (new_ts, r["rid"]))
                changed += 1
                if shown < 4:
                    say(f"   - {table}: '{r['ts']}'  ->  '{new_ts}'")
                    shown += 1
    if changed > shown:
        say(f"   ... and {changed - shown} more rows normalised the same way.")
    return changed


# ---------- duplicate task cleanup ----------

def normalize_title(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def dedupe_tasks(conn):
    rows = conn.execute(
        "SELECT id, course_code, title, assigned_date FROM tasks"
    ).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault((r["course_code"], normalize_title(r["title"])), []).append(r)
    removed = 0
    for (code, _), items in groups.items():
        if len(items) < 2:
            continue
        # Keep the copy that actually has a slot; if tied, keep the lowest id.
        items.sort(key=lambda r: (0 if r["assigned_date"] else 1, r["id"]))
        keep = items[0]
        for dup in items[1:]:
            conn.execute("UPDATE tasks SET status='removed_duplicate' WHERE id=?", (dup["id"],))
            say(f"   - Task #{dup['id']} ({code}) marked as duplicate of #{keep['id']}")
            removed += 1
    return removed


# ---------- give un-slotted tasks a home ----------

SKIP_SLOT_TYPES = {"lecture", "lecture_marathon", "recovery"}


def backfill_slots(conn):
    now = datetime.now(PKT)
    slots = conn.execute("SELECT * FROM weekly_slots").fetchall()
    pending = conn.execute(
        """SELECT id, course_code, est_mins FROM tasks
           WHERE status='pending'
             AND (assigned_date IS NULL OR assigned_date = ''
                  OR assigned_day IS NULL OR assigned_day = ''
                  OR assigned_window IS NULL OR assigned_window = '')"""
    ).fetchall()
    load = {}  # (date, window) -> minutes already placed there by this script
    fixed = 0

    for t in pending:
        best = None
        for offset in range(0, 8):
            cand = now + timedelta(days=offset)
            day_name = cand.strftime("%A")
            date_iso = cand.strftime("%Y-%m-%d")
            for s in slots:
                if s["day_of_week"] != day_name or s["slot_type"] in SKIP_SLOT_TYPES:
                    continue
                try:
                    sh, sm = map(int, s["start_time"].split(":"))
                    eh, em = map(int, s["end_time"].split(":"))
                except (ValueError, TypeError):
                    continue
                slot_start = cand.replace(hour=sh, minute=sm, second=0, microsecond=0)
                slot_end = cand.replace(hour=eh, minute=em, second=0, microsecond=0)
                if slot_end <= now + timedelta(minutes=30):
                    continue
                slot_mins = (eh * 60 + em) - (sh * 60 + sm)
                est = t["est_mins"] or 45
                if slot_mins < est:
                    continue  # task physically doesn't fit in this window
                used = load.get((date_iso, f"{s['start_time']}-{s['end_time']}"), 0)
                if used + est > slot_mins:
                    continue  # this script already filled this window
                if s["primary_course"] == t["course_code"]:
                    score = 50
                elif s["secondary_course"] == t["course_code"]:
                    score = 35
                else:
                    continue  # only place tasks into windows built for their course
                score -= offset * 2  # prefer sooner
                if best is None or score > best[0] or (score == best[0] and slot_start < best[1]):
                    best = (score, slot_start, date_iso, day_name,
                            f"{s['start_time']}-{s['end_time']}", s["location"], est)
        if best:
            _, _, date_iso, day_name, window, loc, est = best
            conn.execute(
                """UPDATE tasks
                   SET assigned_day=?, assigned_date=?, assigned_window=?, assigned_location=?
                   WHERE id=?""",
                (day_name, date_iso, window, loc, t["id"]),
            )
            load[(date_iso, window)] = load.get((date_iso, window), 0) + est
            say(f"   - Task #{t['id']} ({t['course_code']}) slotted: {day_name} {date_iso} {window} @ {loc}")
            fixed += 1
        else:
            say(f"   ! Task #{t['id']} ({t['course_code']}): no fitting window found — Jarvis will handle it once the planner is patched.")
    return fixed


# ---------- main ----------

def main():
    say("=" * 62)
    say("JARVIS // Milestone 0 Migration — 'The Safety Net'")
    say("=" * 62)

    if not DB_PATH.exists():
        say(f"\nERROR: I could not find '{DB_PATH.name}' in:\n  {DB_PATH.parent}")
        say("Put this script in the same folder as your database and try again.")
        sys.exit(1)

    # Step 1 — flush any pending writes, then back up.
    try:
        tmp = sqlite3.connect(DB_PATH)
        tmp.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        tmp.close()
    except sqlite3.Error:
        pass

    stamp = datetime.now(PKT).strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(f"{DB_PATH.stem}_backup_{stamp}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup_path)
    say(f"\n[1/5] Backup created: {backup_path.name}")
    say("      (If anything ever goes wrong, this file is your undo button.)")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Step 2 — health report
        say("\n[2/5] Health check (before repairs):")
        counts = {}
        for table in ("tasks", "episodic_memory", "syllabus_progress",
                      "weekly_slots", "checkins", "courses"):
            counts[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            say(f"   - {table}: {counts[table]} rows")
        pending = conn.execute("SELECT COUNT(*) c FROM tasks WHERE status='pending'").fetchone()["c"]
        unslotted = conn.execute(
            """SELECT COUNT(*) c FROM tasks WHERE status='pending'
               AND (assigned_date IS NULL OR assigned_date='')"""
        ).fetchone()["c"]
        false_quiz = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE is_surprise_quiz=1"
        ).fetchone()["c"]
        say(f"   - pending tasks: {pending} (of which un-slotted: {unslotted})")
        say(f"   - 'surprise quiz' flags currently set: {false_quiz}")
        if counts["syllabus_progress"] == 0:
            say("   - syllabus_progress is EMPTY -> confirms the silent-save bug.")
            say("     (Every syllabus log you ever spoke was rolled back. We fix the cause in Step 2 of M0.)")

        # Step 3 — repairs
        say("\n[3/5] Repairing...")
        removed = dedupe_tasks(conn)
        fixed = backfill_slots(conn)
        cur = conn.execute("UPDATE tasks SET is_surprise_quiz=0 WHERE is_surprise_quiz=1")
        if cur.rowcount:
            say(f"   - Cleared {cur.rowcount} false 'surprise quiz' flag(s).")
        ts_changed = fix_timestamps(conn)

        # Step 4 — version tag + indexes
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            notes TEXT
        )""")
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at, notes) VALUES (4, ?, ?)",
            (datetime.now(PKT).strftime("%Y-%m-%dT%H:%M:%S+05:00"),
             "M0 hotfix: backup, dedupe, backfill, timestamp normalisation"),
        )
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_date)",
            "CREATE INDEX IF NOT EXISTS idx_slots_day ON weekly_slots(day_of_week)",
            "CREATE INDEX IF NOT EXISTS idx_checkins_ts ON checkins(timestamp)",
        ):
            conn.execute(stmt)

        conn.commit()
        say("\n[4/5] All changes saved.")
        say(f"   - duplicates removed: {removed}")
        say(f"   - tasks slotted: {fixed}")
        say(f"   - timestamps normalised: {ts_changed}")

    except sqlite3.Error as e:
        conn.rollback()
        say(f"\nSomething went wrong: {e}")
        say("No changes were saved. Your database and backup are untouched.")
        say("(If the error mentions 'locked', make sure Jarvis/the server is fully closed and retry.)")
        conn.close()
        sys.exit(1)
    conn.close()

    say("\n[5/5] Done! Your data is backed up and repaired.")
    say("Paste this whole output back in our chat, and we move to Mission 2. 🎩")

    report_path = DB_PATH.with_name("migration_report.txt")
    report_path.write_text("\n".join(REPORT), encoding="utf-8")


if __name__ == "__main__":
    main()
