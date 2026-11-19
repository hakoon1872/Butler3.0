"""
JARVIS — assembler.py  (audit item #8 completed + item #12: academics end-to-end)
================================================================================
Plain English: this is what Jarvis "wakes up knowing". Every time a session
starts, this file builds his briefing packet from the database — identity,
the truth about grades, today's plan, open promises, what he can do, fresh
state, remembered facts, and the tail of the last conversation.

Audit rules it obeys:
  * P1  "Data, not code": change a mark in the DB -> the next packet changes.
        No code edit anywhere. (This is M2's exit test.)
  * §5  Fixed token budget. ALWAYS: identity, today's plan, open commitments,
        capability manifest (+ academic truth and state). THEN, while budget
        lasts: top facts by importance x recency, last session summary,
        ~6 raw turns. Vault facts and off-record turns NEVER enter the packet.
  * G5  State is labelled with its age; stale state says "re-ask".
  * B2  Task ordering never lets unslotted legacy tasks crowd out real ones.

Token counts are estimates (chars / 4) — no tokenizer dependency, honest
enough for budgeting, and the estimate is reported so nothing is hidden.
Read-only: this file never writes anything except seeding its own budget row.
"""

import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

import database as db
import calculators

DEFAULT_TOKEN_BUDGET = 4000
FACT_CANDIDATES = 30          # ranked, then added one-by-one while budget lasts
RAW_TURNS = 6                 # audit §5: "about six raw turns"
TURN_CHAR_CAP = 240


def _est(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, (len(text) + 3) // 4)


def _budget() -> int:
    # data, not code: editable row; seeded once with the default
    db.write("INSERT OR IGNORE INTO system_config (key,value,updated_at) VALUES ('context_token_budget',?,?)",
             (str(DEFAULT_TOKEN_BUDGET), db.iso_utc()))
    try:
        return max(200, int(db.scalar("SELECT value FROM system_config WHERE key='context_token_budget'",
                                      default=str(DEFAULT_TOKEN_BUDGET))))
    except (TypeError, ValueError):
        return DEFAULT_TOKEN_BUDGET


# ---------------------------------------------------------------------------
# today_picture(): one source of truth for "today", used by BOTH the wake
# packet and /api/dashboard — so the screen and the brain never disagree.
# ---------------------------------------------------------------------------
def today_picture() -> Dict[str, Any]:
    now = db.now_pkt()
    day = now.strftime("%A")
    today_iso = now.strftime("%Y-%m-%d")
    blocks = db.rows("""SELECT * FROM time_blocks WHERE day_of_week=? AND active=1
                        ORDER BY start_time""", (day,))
    # B2-safe ordering: surprise quizzes first, then real deadlines (NULLs LAST),
    # then tier, then age. Unslotted tasks can never crowd out slotted ones.
    pending = db.rows("""SELECT t.*, c.priority_tier FROM tasks t
        LEFT JOIN courses c ON t.course_code=c.code
        WHERE t.status IN ('pending','scheduled','in_progress')
        ORDER BY t.is_surprise_quiz DESC, (t.deadline_at IS NULL), t.deadline_at ASC,
                 c.priority_tier ASC, t.id ASC""")
    today_tasks = [t for t in pending if (t.get("assigned_date") or "")[:10] == today_iso]
    horizon = db.iso_utc(now + timedelta(hours=48))
    due_soon = [t for t in pending
                if t.get("deadline_at") and t["deadline_at"] <= horizon
                and t not in today_tasks]
    unslotted = [t for t in pending if not t.get("assigned_date")]
    return {
        "day": day, "date": today_iso, "now_pkt": now.strftime("%Y-%m-%d %H:%M PKT"),
        "blocks": blocks, "today_tasks": today_tasks,
        "due_soon": due_soon[:8], "unslotted_count": len(unslotted),
        "unslotted_top": [{"id": t["id"], "title": t["title"], "course_code": t["course_code"]}
                          for t in unslotted[:5]],
        "pending_count": len(pending),
    }


# ---------------------------------------------------------------------------
# section builders — each returns (title, text)
# ---------------------------------------------------------------------------
def _identity() -> str:
    p = db.row("SELECT * FROM profile WHERE id=1") or {}
    name = p.get("preferred_name") or p.get("name") or "Haider"
    bits = [f"You are Jarvis, the academic butler of {name}"]
    if p.get("name") and p.get("name") != name:
        bits[0] += f" ({p['name']})"
    if p.get("program"):
        bits.append(f"{p['program']}, semester {p.get('semester', '?')}"
                    + (f" at {p['university']}" if p.get("university") else ""))
    if p.get("current_gpa") is not None:
        bits.append(f"current GPA {p['current_gpa']}, target {p.get('target_gpa', 4.0)}")
    bits.append("Timezone Asia/Karachi (PKT). Speak plainly; arithmetic comes from tools, never improvise it.")
    return ". ".join(bits) + "."


def _academics() -> str:
    margins = calculators.calculate_grade_margins()
    lines: List[str] = []
    if isinstance(margins, dict):          # e.g. no grading scale yet
        return margins.get("say", "Academics not configured.")
    feas = calculators.grade_feasibility()
    headlines = {c["code"]: c["say"] for c in feas.get("courses", [])} if feas.get("ok") else {}
    for m in margins:
        line = (f"{m['code']} ({m['name']}, {m['credits']}cr, tier {m['tier']}): "
                f"banked {m['obtained_sum']}/{m['max_total']}, projected {m['projected_pct']}% "
                f"{m['projected_grade']}, ceiling {m['ceiling_pct']}% {m['ceiling_grade']}.")
        if m["code"] in headlines:
            line += " " + headlines[m["code"]].split(": ", 1)[-1]
        if m.get("study_protocol"):
            line += f" Protocol: {m['study_protocol']}."
        if m.get("risk_flags"):
            line += f" Risks: {m['risk_flags']}."
        if m.get("warning"):
            line += f" WARNING: {m['warning']}"
        lines.append(line)
    gpa = calculators.gpa_projection()
    if gpa.get("ok"):
        lines.append(f"GPA: projected {gpa['projected_gpa']}, floor {gpa['floor_gpa']}, "
                     f"ceiling {gpa['ceiling_gpa']}"
                     + (f", target {gpa['target_gpa']}" if gpa.get("target_gpa") is not None else "") + ".")
    va = calculators.validate_allocations()
    if va.get("ok") and not va.get("all_courses_valid"):
        bad = [c["say"] for c in va["courses"] if not c["ok"]]
        lines.append("MARKS SETUP ISSUES (fix before trusting grades): " + " | ".join(bad))
    return "\n".join(lines) if lines else "No courses configured."


def _plan(pic: Dict[str, Any]) -> str:
    lines = [f"Today is {pic['day']} {pic['date']}, {pic['now_pkt']}."]
    if pic["blocks"]:
        lines.append("Blocks: " + "; ".join(
            f"{b['start_time']}-{b['end_time']} {b['type']}"
            + (f"@{b['location']}" if b.get("location") else "")
            + (f" ({b['course_focus']})" if b.get("course_focus") else "")
            for b in pic["blocks"]))
    else:
        lines.append("No time blocks set for today.")
    if pic["today_tasks"]:
        lines.append("Scheduled today: " + "; ".join(
            f"[{t['id']}] {t['title']}"
            + (f" ({t['course_code']})" if t.get("course_code") else "")
            + (f" {t['assigned_window']}" if t.get("assigned_window") else "")
            + (f" @{t['assigned_location']}" if t.get("assigned_location") else "")
            for t in pic["today_tasks"]))
    if pic["due_soon"]:
        lines.append("Due within 48h: " + "; ".join(
            f"[{t['id']}] {t['title']} (deadline {db.fmt_pkt(t['deadline_at'], '%a %H:%M')})"
            for t in pic["due_soon"]))
    if pic["unslotted_count"]:
        tops = ", ".join(f"[{t['id']}] {t['title']}" for t in pic["unslotted_top"])
        lines.append(f"{pic['unslotted_count']} pending tasks have NO slot (offer to schedule): {tops}.")
    return "\n".join(lines)


def _commitments() -> str:
    rows = db.rows("SELECT * FROM commitments WHERE status='open' ORDER BY due_at IS NULL, due_at ASC LIMIT 10")
    if not rows:
        return "No open commitments."
    return "Open promises: " + "; ".join(
        f"[{r['id']}] {r['promise']}" + (f" (due {r['due_at'][:16]})" if r.get("due_at") else "")
        + f" L{r['ladder_level']}" for r in rows)


def _manifest() -> str:
    tools = db.rows("SELECT name, risk FROM tool_specs WHERE enabled=1 ORDER BY name")
    if not tools:
        return "Tool manifest empty — registry not loaded."
    by_risk: Dict[str, int] = {}
    for t in tools:
        by_risk[t["risk"]] = by_risk.get(t["risk"], 0) + 1
    destr = [t["name"] for t in tools if t["risk"] == "destructive"]
    return (f"You have {len(tools)} tools ({', '.join(f'{v} {k}' for k, v in sorted(by_risk.items()))}). "
            f"Destructive tools ({', '.join(destr) or 'none'}) require Haider's explicit yes. "
            "Names: " + ", ".join(t["name"] for t in tools) + ".")


def _state() -> str:
    s = db.get_working_state()
    txt = (f"State as of {s.get('updated_at_pkt', '?')} ({s.get('age_minutes', '?')} min ago): "
           f"{s.get('location')}, energy {s.get('energy')}/5, hydration {s.get('hydration')}/5, "
           f"fed {'yes' if s.get('fed') else 'no'}, stress {s.get('stress')}/5, "
           f"doing: {s.get('current_activity')}.")
    if s.get("is_stale"):
        txt += " STALE — ask one fresh check-in question before relying on this."
    return txt


def _fact_lines() -> List[str]:
    rows = db.rows("""SELECT * FROM facts WHERE status='active' AND sensitivity!='vault'
                      ORDER BY importance DESC, COALESCE(last_verified_at, created_at) DESC
                      LIMIT ?""", (FACT_CANDIDATES,))
    now = db.utc_now()
    out = []
    for r in rows:
        stale = ""
        verified = db.parse_ts(r.get("last_verified_at") or r.get("created_at"))
        if verified and r.get("verify_after_days") is not None:
            if verified + timedelta(days=float(r["verify_after_days"])) < now:
                stale = " (stale — re-verify)"
        out.append(f"[{r['domain']}] {r['key']}: {r['value']}{stale}")
    return out


def _last_summary() -> Optional[str]:
    r = db.row("SELECT * FROM session_summaries ORDER BY id DESC LIMIT 1")
    if not r:
        return None
    txt = "Last session summary: " + r["summary"]
    if r.get("open_threads"):
        txt += " Open threads: " + r["open_threads"]
    return txt


def _recent_turns() -> List[str]:
    rows = db.rows("""SELECT role, content FROM turns
                      WHERE off_record=0 AND sensitivity!='vault' AND role IN ('user','jarvis')
                      ORDER BY id DESC LIMIT ?""", (RAW_TURNS,))
    out = []
    for r in reversed(rows):
        content = (r["content"] or "").strip().replace("\n", " ")
        if len(content) > TURN_CHAR_CAP:
            content = content[:TURN_CHAR_CAP] + "…"
        out.append(f"{r['role']}: {content}")
    return out


# ---------------------------------------------------------------------------
# the assembler itself
# ---------------------------------------------------------------------------
def assemble_wake_context() -> Dict[str, Any]:
    """Build the wake packet under a fixed token budget.
    Returns {text, est_tokens, budget, sections:[{name,tokens,included}], generated_at}."""
    budget = _budget()
    pic = today_picture()
    sections: List[Dict[str, Any]] = []
    parts: List[str] = []
    used = 0

    def add(name: str, text: str, always: bool = False) -> bool:
        nonlocal used
        t = _est(text)
        if not always and used + t > budget:
            sections.append({"name": name, "tokens": t, "included": False})
            return False
        parts.append(f"## {name}\n{text}")
        used += t + _est(f"## {name}\n")
        sections.append({"name": name, "tokens": t, "included": True})
        return True

    # ALWAYS (audit §5): identity, plan, commitments, manifest — plus the
    # academic truth (M2) and state-with-age (G5). These are kept compact.
    add("IDENTITY", _identity(), always=True)
    add("ACADEMIC TRUTH", _academics(), always=True)
    add("TODAY", _plan(pic), always=True)
    add("COMMITMENTS", _commitments(), always=True)
    add("CAPABILITIES", _manifest(), always=True)
    add("STATE", _state(), always=True)

    # OPTIONAL, in priority order, while the budget lasts.
    facts = _fact_lines()
    if facts:
        kept = []
        for line in facts:
            if used + _est(line) + 2 > budget:
                break
            kept.append(line)
            used += _est(line) + 2
        if kept:
            parts.append("## REMEMBERED FACTS\n" + "\n".join(kept))
            sections.append({"name": "REMEMBERED FACTS", "tokens": sum(_est(k) for k in kept),
                             "included": True, "kept": len(kept), "candidates": len(facts)})
        else:
            sections.append({"name": "REMEMBERED FACTS", "tokens": sum(_est(k) for k in facts),
                             "included": False})
    summ = _last_summary()
    if summ:
        add("LAST SESSION", summ)
    turns = _recent_turns()
    if turns:
        add("RECENT TURNS", "\n".join(turns))

    return {"text": "\n\n".join(parts), "est_tokens": used, "budget": budget,
            "sections": sections, "generated_at": db.iso_utc()}
