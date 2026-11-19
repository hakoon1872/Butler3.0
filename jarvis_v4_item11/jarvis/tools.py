from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from database import get_conn, now_pkt, get_working_state, resolve_deadline  # N1 import fix; B5: use the one true date parser

DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

# ---------- Grade Engine ----------
def calculate_grade_margins() -> List[Dict[str, Any]]:
    conn = get_conn()
    courses = conn.execute("SELECT * FROM courses ORDER BY priority_tier ASC, time_share_pct DESC").fetchall()
    scale = conn.execute("SELECT * FROM grading_scale ORDER BY threshold DESC").fetchall()
    conn.close()

    def grade_for(pct):
        for s in scale:
            if pct >= s["threshold"]:
                return {"letter": s["letter"], "gp": s["points"]}
        return {"letter": "F", "gp": 0.0}

    results = []
    for c in courses:
        conn = get_conn()
        assessments = conn.execute("SELECT * FROM assessments WHERE course_code=?", (c["code"],)).fetchall()
        conn.close()
        obtained_sum = sum(a["obtained"] for a in assessments if a["status"] == "Completed")
        expected_sum = sum(a["expected"] for a in assessments if a["status"] in ("Upcoming","In Progress"))
        max_so_far = sum(a["max_marks"] for a in assessments if a["status"] == "Completed")
        max_total = sum(a["max_marks"] for a in assessments)
        projected = obtained_sum + expected_sum
        projected_pct = (projected / max_total * 100) if max_total > 0 else 0
        grade = grade_for(projected_pct)
        results.append({
            "code": c["code"], "name": c["name"], "instructor": c["instructor"],
            "credits": c["credits"], "tier": c["priority_tier"], "time_share_pct": c["time_share_pct"],
            "obtained_sum": round(obtained_sum, 2), "expected_sum": round(expected_sum, 2),
            "max_so_far": round(max_so_far, 2), "max_total": round(max_total, 2),
            "projected_total": round(projected, 2), "projected_pct": round(projected_pct, 2),
            "projected_grade": grade["letter"], "projected_gp": grade["gp"],
            "study_protocol": c["study_protocol"], "risk_flags": c["risk_flags"],
        })
    return results

# ---------- Dashboard State ----------
def get_full_dashboard_state() -> Dict[str, Any]:
    now = now_pkt()
    day_name = now.strftime("%A")
    conn = get_conn()
    profile = conn.execute("SELECT * FROM profile WHERE id=1").fetchone()
    config = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM system_config").fetchall()}
    tasks = [dict(r) for r in conn.execute("""SELECT t.*, c.priority_tier FROM tasks t
        LEFT JOIN courses c ON t.course_code = c.code WHERE t.status='pending'
        ORDER BY t.is_surprise_quiz DESC, t.assigned_date ASC, c.priority_tier ASC, t.id ASC""").fetchall()]
    syllabus = [dict(r) for r in conn.execute("SELECT * FROM syllabus_progress ORDER BY id DESC LIMIT 15").fetchall()]
    goals = [dict(r) for r in conn.execute("SELECT * FROM goals").fetchall()]
    time_blocks = [dict(r) for r in conn.execute("SELECT * FROM time_blocks WHERE day_of_week=? ORDER BY start_time", (day_name,)).fetchall()]
    conn.close()
    return {
        "timestamp_pkt": now.strftime("%A, %d %b %Y • %I:%M %p PKT"),
        "day_of_week": day_name,
        "profile": dict(profile) if profile else {},
        "config": config,
        "voice_name": config.get("voice_name", "Fenrir"),
        "working_state": get_working_state(),
        "today_blocks": time_blocks,
        "pending_tasks": tasks,
        "syllabus_progress": syllabus,
        "goals": goals,
        "grade_margins": calculate_grade_margins(),
        "completed_tasks_count": get_conn().execute("SELECT COUNT(*) FROM tasks WHERE status='completed'").fetchone()[0],
    }

# ---------- Slot Finder ----------
def find_best_slot_for_task(course_code: str, est_mins: int = 60, deadline_hint: Optional[str] = None) -> Dict[str, str]:
    now = now_pkt()
    deadline_dt = _resolve_deadline(deadline_hint)
    conn = get_conn()
    blocks = conn.execute("SELECT * FROM time_blocks").fetchall()
    load = {(r["assigned_date"], r["assigned_window"]): r["mins"] for r in conn.execute(
        "SELECT assigned_date, assigned_window, COALESCE(SUM(est_mins),0) AS mins FROM tasks WHERE status='pending' GROUP BY assigned_date, assigned_window").fetchall()}
    conn.close()
    code = course_code.upper().strip()
    candidates = []
    for offset in range(0, 8):
        cand = now + timedelta(days=offset)
        day_str = cand.strftime("%A")
        date_iso = cand.strftime("%Y-%m-%d")
        for b in blocks:
            if b["day_of_week"] != day_str or b["type"] in ("Lecture","Lab","Sleep","Meal","Commute"):
                continue
            sh, sm = map(int, b["start_time"].split(":"))
            eh, em = map(int, b["end_time"].split(":"))
            slot_start = cand.replace(hour=sh, minute=sm)
            slot_end = cand.replace(hour=eh, minute=em)
            if slot_end <= now + timedelta(minutes=15):
                continue
            if deadline_dt and slot_start >= deadline_dt:
                continue
            slot_mins = (eh*60+em) - (sh*60+sm)
            if slot_mins < est_mins:
                continue
            window = f"{b['start_time']}-{b['end_time']}"
            used = load.get((date_iso, window), 0)
            if used + est_mins > slot_mins:
                continue
            score = 0
            if b.get("course_focus") == code:
                score += 50
            elif b["type"] in ("Deep Work","Light Review"):
                score += 25
            score -= int(used / 15)
            score -= offset * 2
            candidates.append((score, date_iso, day_str, window, b.get("location","Home")))
    if not candidates:
        return {"assigned_day":"Tomorrow","assigned_date":(now+timedelta(days=1)).strftime("%Y-%m-%d"),
                "assigned_window":"21:00-23:00","assigned_location":"Home","reason":"Overflow slot"}
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]
    return {"assigned_date":best[1],"assigned_day":best[2],"assigned_window":best[3],
            "assigned_location":best[4],"reason":"Auto-slotted"}

def _resolve_deadline(hint: Optional[str]) -> Optional[datetime]:
    """B5 fix: delegate to database.resolve_deadline — the fixed parser (handles
    'today', 'tomorrow 9am', 'Nov 18', weekday+time correctly). One source of truth."""
    return resolve_deadline(hint)

# ---------- Working State ----------
def patch_working_state(location=None, energy=None, hydration=None, fed=None, stress=None, current_activity=None) -> Dict[str, Any]:
    cur_state = get_working_state()
    new_loc = location.strip() if location else cur_state["location"]
    new_energy = max(1, min(5, int(energy))) if energy is not None else cur_state["energy"]
    new_hyd = max(0, min(5, int(hydration))) if hydration is not None else cur_state["hydration"]
    new_fed = (1 if fed else 0) if fed is not None else cur_state["fed"]
    new_stress = max(1, min(5, int(stress))) if stress is not None else cur_state["stress"]
    new_act = current_activity.strip() if current_activity else cur_state["current_activity"]
    ts = now_pkt().strftime("%Y-%m-%d %H:%M:%S %Z")
    conn = get_conn()
    conn.execute("""UPDATE working_state SET location=?, energy=?, hydration=?, fed=?, stress=?, current_activity=?, updated_at=? WHERE id=1""",
                 (new_loc, new_energy, new_hyd, new_fed, new_stress, new_act, ts))
    conn.commit()
    conn.close()
    return {"status": "state_updated", "working_state": {"location":new_loc,"energy":new_energy,"hydration":new_hyd,"fed":new_fed,"stress":new_stress,"current_activity":new_act,"updated_at":ts}}

# ---------- Task Management ----------
def add_task(course_code: str, title: str, est_mins: int = 45, deadline_hint: Optional[str] = None, is_surprise_quiz: bool = False) -> Dict[str, Any]:
    code = course_code.upper().strip() if course_code else None
    slot = find_best_slot_for_task(code or "GEN", est_mins=est_mins, deadline_hint=deadline_hint)
    conn = get_conn()
    cur = conn.execute("""INSERT INTO tasks (course_code, title, est_mins, is_surprise_quiz, due_date, assigned_day, assigned_date, assigned_window, assigned_location, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (code, title, int(est_mins), 1 if is_surprise_quiz else 0, deadline_hint,
         slot["assigned_day"], slot["assigned_date"], slot["assigned_window"], slot["assigned_location"], now_pkt().isoformat()))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"status": "task_added", "task_id": task_id, "course_code": code, "title": title, "scheduled_slot": slot}

def complete_task(task_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (int(task_id),))
    conn.commit()
    conn.close()
    return {"status": "task_completed", "task_id": task_id}

def delete_task(task_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM tasks WHERE id=?", (int(task_id),))
    conn.commit()
    conn.close()
    return {"status": "task_deleted", "task_id": task_id}

# ---------- Syllabus & Scheduling ----------
def log_syllabus_and_schedule(course_code: str, chapter_topic: str, taught_scope: str, personal_target: str,
                              deadline_hint: Optional[str] = None, est_mins: int = 60) -> Dict[str, Any]:
    code = course_code.upper().strip()
    ts = now_pkt().strftime("%Y-%m-%d %H:%M %Z")
    conn = get_conn()
    conn.execute("""INSERT INTO syllabus_progress (course_code, chapter_topic, taught_scope, personal_target, deadline_label, updated_at)
        VALUES (?,?,?,?,?,?)""", (code, chapter_topic, taught_scope, personal_target, deadline_hint or "Flexible", ts))
    conn.commit()
    conn.close()
    slot = find_best_slot_for_task(code, est_mins=est_mins, deadline_hint=deadline_hint)
    task_title = f"{chapter_topic}: {personal_target} (Taught: {taught_scope})"
    conn = get_conn()
    cur = conn.execute("""INSERT INTO tasks (course_code, title, est_mins, due_date, assigned_day, assigned_date, assigned_window, assigned_location, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (code, task_title, int(est_mins), deadline_hint, slot["assigned_day"], slot["assigned_date"], slot["assigned_window"], slot["assigned_location"], now_pkt().isoformat()))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"status": "syllabus_logged_and_scheduled", "task_id": task_id, "course_code": code, "scheduled_slot": slot}

# ---------- Schedule Management ----------
def get_weekly_schedule(day_of_week: Optional[str] = None) -> Dict[str, Any]:
    conn = get_conn()
    if day_of_week:
        rows = conn.execute("SELECT * FROM time_blocks WHERE day_of_week=? ORDER BY start_time", (day_of_week.strip().capitalize(),)).fetchall()
    else:
        rows = conn.execute("""SELECT * FROM time_blocks ORDER BY CASE day_of_week
            WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4
            WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 WHEN 'Sunday' THEN 7 END, start_time""").fetchall()
    conn.close()
    return {"filter_day": day_of_week or "All Week", "slots": [dict(r) for r in rows]}

# ---------- Tool Registry ----------
TOOL_REGISTRY = {
    "get_full_dashboard_state": get_full_dashboard_state,
    "calculate_grade_margins": calculate_grade_margins,
    "patch_working_state": patch_working_state,
    "add_task": add_task,
    "complete_task": complete_task,
    "delete_task": delete_task,
    "log_syllabus_and_schedule": log_syllabus_and_schedule,
    "get_weekly_schedule": get_weekly_schedule,
    "find_best_slot_for_task": find_best_slot_for_task,
}
