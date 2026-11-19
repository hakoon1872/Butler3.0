"""
Jarvis v4 — TOOL REGISTRY
=========================
Audit build queue item #10 (milestone M2). In the §9 package layout this file is
`tools/registry.py`; it lives at the project root for now because the project is
still flat (a `tools/` folder would collide with the existing `tools.py`).

WHAT THIS FILE IS, IN PLAIN ENGLISH
-----------------------------------
Until now Jarvis's tools were a hand-written list. That is how the audit's B6
happened: the code knew 15 tools, the spec promised 17, and nobody noticed.

This file is the single front desk. Every tool Jarvis can use is described here
once, as data. From that one description we generate:
  * the list Google Gemini is told about when a session connects,
  * Jarvis's own self-description ("what can you actually do?"),
  * the safety rules (which tools need your permission),
  * the plumbing (argument checking, error catching, audit trail).

Four rules it enforces (audit §4):
  1. The LLM proposes, code disposes  -> arguments are validated here, not trusted.
  2. Data, not code                   -> specs are rows in `tool_specs`, editable.
  3. Every write is attributable      -> write tools land in `audit_log`.
  4. You approve what changes Jarvis  -> destructive tools need confirmation.

It also fixes, without touching tools.py:
  N5  state updates now go through database.set_working_state(), so energy/stress
      get a timestamp and a shelf-life (state_signals) instead of being written raw.
  N6  recording a mark twice updates the existing row instead of creating a second
      one that double-counts your grade.

Marks policy (confirmed by Haider, 16 Nov 2026): marks are WEIGHTED POINTS.
  4 quizzes x 3.75 = 15 | 4 assignments x 2.5 = 10 | midterm 25 | final 50 = 100.
  So `assessments.max_marks` IS the weight; no separate weight column is needed.
  The calculators (build item #11) will assert each course sums to 100.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import database as db

# ---------------------------------------------------------------------------
# 1. The spec shape (audit §7.1)
# ---------------------------------------------------------------------------

READ, WRITE, DESTRUCTIVE = "read", "write", "destructive"
BLOCKING, NON_BLOCKING = "BLOCKING", "NON_BLOCKING"
INTERRUPT, WHEN_IDLE, SILENT = "INTERRUPT", "WHEN_IDLE", "SILENT"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str                      # what Gemini reads to decide when to call it
    handler: str                          # "module:function" — where the work actually happens
    params: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    risk: str = READ
    confirmation: str = "never"           # never | always
    behaviour: str = BLOCKING             # BLOCKING | NON_BLOCKING
    scheduling: str = WHEN_IDLE           # INTERRUPT | WHEN_IDLE | SILENT
    domain: str = "*"


def _obj(props: Dict[str, Any], required: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Tiny helper so the spec list below stays readable."""
    return {"type": "object", "properties": props, "required": list(required)}


S = {"type": "string"}
I = {"type": "integer"}
N = {"type": "number"}
B = {"type": "boolean"}
SCALE = {"type": "integer", "minimum": 1, "maximum": 5}


# ---------------------------------------------------------------------------
# 2. A few thin wrappers (the only business logic in this file)
# ---------------------------------------------------------------------------

def _patch_state(location=None, energy=None, hydration=None, fed=None, stress=None,
                 current_activity=None) -> Dict[str, Any]:
    """N5 fix: route through the memory layer so state gets TTL + freshness."""
    return db.set_working_state(location=location, energy=energy, hydration=hydration,
                                fed=fed, stress=stress, current_activity=current_activity,
                                source="stated")


def _search_memory(query: str, limit: int = 8) -> Dict[str, Any]:
    """One recall tool over both memory layers: facts and past conversations."""
    return {"facts": db.search_facts(query, limit=limit),
            "past_turns": db.search_turns(query, limit=limit)}


def _whats_due(limit: int = 5) -> Dict[str, Any]:
    return {"facts_to_reverify": db.facts_due_for_review(limit),
            "spaced_reviews": db.due_spaced_reviews(limit)}


def _set_voice_name(voice_name: str) -> Dict[str, Any]:
    """The audit flagged this as promised-but-uncallable. It is a config row, not code."""
    db.set_config("voice_name", voice_name, actor="haider")
    return {"voice_name": voice_name, "status": "saved"}


def _record_assessment(course_code: str, label: str, type_: str, max_marks: float,
                       obtained: float = 0, expected: float = 0, status: str = "Upcoming",
                       due_hint: Optional[str] = None) -> Dict[str, Any]:
    """N6 guard: one row per (course, assessment label). Re-reporting a mark updates it."""
    code = course_code.upper().strip()
    existing = db.row("SELECT id FROM assessments WHERE course_code=? AND label=?", (code, label))
    if existing:
        dl = db.resolve_deadline(due_hint) if due_hint else None
        db.write("""UPDATE assessments SET type=?, max_marks=?, obtained=?, expected=?, status=?,
                    due_at=COALESCE(?, due_at), recorded_at=? WHERE id=?""",
                 (type_, max_marks, obtained, expected, status,
                  db.iso_utc(dl) if dl else None, db.iso_utc(), existing["id"]))
        db.audit("assessment_update", "assessments", existing["id"],
                 after={"course": code, "label": label, "obtained": obtained, "max": max_marks})
        return {"assessment_id": existing["id"], "status": "updated", "course_code": code, "label": label}
    aid = db.record_assessment(code, label, type_, max_marks, obtained=obtained,
                               expected=expected, status=status, due_hint=due_hint)
    return {"assessment_id": aid, "status": "created", "course_code": code, "label": label}


# ---------------------------------------------------------------------------
# 3. THE SPEC LIST — the single source of truth (26 tools; the spec asked for 17)
# ---------------------------------------------------------------------------

SPECS: List[ToolSpec] = [
    # ---- READ: safe, instant, never asks permission -------------------------
    ToolSpec("get_full_dashboard_state",
             "Full current picture: profile, today's blocks, pending tasks, grades, state. Call at wake.",
             "tools:get_full_dashboard_state", risk=READ),
    ToolSpec("calculate_grade_margins",
             "Per-course marks so far, projection and letter grade (fixed engine: banked marks always count, cancelled work excluded).",
             "calculators:calculate_grade_margins", risk=READ),
    ToolSpec("grade_feasibility",
             "Truth table per course: which grades are secured, still reachable, or mathematically impossible, with the marks each still costs.",
             "calculators:grade_feasibility", _obj({"course_code": S}), risk=READ),
    ToolSpec("required_on_final",
             "Exact final-exam mark needed for a target grade (or every tier). States its assumption: other pending work scores expected marks.",
             "calculators:required_on_final",
             _obj({"course_code": S, "target_letter": S}, ("course_code",)), risk=READ),
    ToolSpec("validate_allocations",
             "Check every course's assessment weights against the 15/10/25/50 policy (4 quizzes x3.75, 4 assignments x2.5, mid 25, final 50 = 100). Call after setup or marks edits.",
             "calculators:validate_allocations", risk=READ),
    ToolSpec("gpa_projection",
             "Credit-weighted GPA: projected, floor (zeros from here) and ceiling (perfect finish), versus target.",
             "calculators:gpa_projection", risk=READ),
    ToolSpec("get_weekly_schedule",
             "The weekly time-block template, optionally for one day.",
             "tools:get_weekly_schedule", _obj({"day_of_week": S}), risk=READ),
    ToolSpec("find_best_slot_for_task",
             "Find the best free slot for a task of a given length before a deadline.",
             "tools:find_best_slot_for_task",
             _obj({"course_code": S, "est_mins": I, "deadline_hint": S}, ("course_code",)), risk=READ),
    ToolSpec("search_memory",
             "Recall anything: facts about Haider and past conversations. Use before saying 'I don't remember'.",
             "registry:_search_memory", _obj({"query": S, "limit": I}, ("query",)), risk=READ),
    ToolSpec("whats_due",
             "Facts that need re-confirming and spaced reviews that are due.",
             "registry:_whats_due", _obj({"limit": I}), risk=READ),
    ToolSpec("get_capability_manifest",
             "Jarvis's own self-model: tools, limits, domains, policies. Use instead of guessing what you can do.",
             "database:capability_manifest", risk=READ),
    ToolSpec("session_history",
             "Past sessions with their summaries.",
             "database:session_history", _obj({"limit": I}), risk=READ),

    # ---- WRITE: changes data, runs in the background, no permission needed --
    ToolSpec("add_task",
             "Add a task and auto-slot it. deadline_hint understands 'today', 'tomorrow 9am', 'Nov 18'.",
             "tools:add_task",
             _obj({"course_code": S, "title": S, "est_mins": I, "deadline_hint": S,
                   "is_surprise_quiz": B}, ("title",)),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE, domain="Academics"),
    ToolSpec("complete_task", "Mark a task done.", "tools:complete_task",
             _obj({"task_id": I}, ("task_id",)),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE),
    ToolSpec("log_syllabus_and_schedule",
             "Log what was taught, what Haider will self-study, and schedule it.",
             "tools:log_syllabus_and_schedule",
             _obj({"course_code": S, "chapter_topic": S, "taught_scope": S, "personal_target": S,
                   "deadline_hint": S, "est_mins": I},
                  ("course_code", "chapter_topic", "taught_scope", "personal_target")),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE, domain="Academics"),
    ToolSpec("patch_working_state",
             "Update where Haider is and how he is doing: location, energy, hydration, fed, stress, activity.",
             "registry:_patch_state",
             _obj({"location": S, "energy": SCALE, "hydration": {"type": "integer", "minimum": 0, "maximum": 5},
                   "fed": B, "stress": SCALE, "current_activity": S}),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=SILENT),
    ToolSpec("record_checkin",
             "Store a check-in, including what focus it produced.",
             "database:record_checkin",
             _obj({"location": S, "energy": SCALE, "hydration": I, "fed": B, "stress": SCALE,
                   "activity": S, "notes": S, "resulting_focus": S}),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=SILENT),
    ToolSpec("remember",
             "Save a durable fact about Haider with evidence. source: stated | observed | inferred.",
             "database:remember",
             _obj({"domain": S, "key": S, "value": S,
                   "source": {"type": "string", "enum": ["stated", "observed", "inferred"]},
                   "evidence": S, "importance": SCALE,
                   "sensitivity": {"type": "string", "enum": ["normal", "private", "vault"]}},
                  ("domain", "key", "value")),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE),
    ToolSpec("confirm_fact", "Haider confirmed a proposed fact; make it active.",
             "database:confirm_fact", _obj({"fact_id": I, "value": S}, ("fact_id",)),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE),
    ToolSpec("record_assessment",
             "Record or correct a mark. max_marks is the WEIGHTED allotment "
             "(quiz 3.75, assignment 2.5, midterm 25, final 50).",
             "registry:_record_assessment",
             _obj({"course_code": S, "label": S,
                   "type_": {"type": "string", "enum": ["Quiz", "Assignment", "Midterm", "Final",
                                                        "Presentation", "Project", "Other"]},
                   "max_marks": N, "obtained": N, "expected": N,
                   "status": {"type": "string", "enum": ["Upcoming", "In Progress", "Completed",
                                                         "Missed", "Cancelled"]},
                   "due_hint": S},
                  ("course_code", "label", "type_", "max_marks")),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE, domain="Academics"),
    ToolSpec("log_lecture_debrief",
             "After a lecture: topic, what was taught, confusion points, instructor hints.",
             "database:log_lecture_debrief",
             _obj({"course_code": S, "topic": S, "taught_scope": S, "confusion_points": S,
                   "instructor_hints": S}, ("course_code", "topic")),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE, domain="Academics"),
    ToolSpec("log_study_session",
             "Log a completed study block: method, minutes, retention confidence.",
             "database:log_study_session",
             _obj({"course_code": S, "topic": S, "method_used": S, "duration_mins": I,
                   "retention_confidence": SCALE,
                   "outcome": {"type": "string", "enum": ["done", "partial", "missed"]}},
                  ("course_code", "topic")),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE, domain="Academics"),
    ToolSpec("record_commitment", "Haider promised something. Open a commitment in the ledger.",
             "database:record_commitment",
             _obj({"promise": S, "domain": S, "task_id": I, "due_at": S}, ("promise",)),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE),
    ToolSpec("record_outcome",
             "Close the loop on a block or commitment: done / partial / missed, plus the reason.",
             "database:record_outcome",
             _obj({"result": {"type": "string", "enum": ["done", "partial", "missed"]},
                   "commitment_id": I, "plan_item_id": I, "task_id": I, "reason": S, "actual_mins": I},
                  ("result",)),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE),
    ToolSpec("set_voice_name", "Change the voice Jarvis speaks with.",
             "registry:_set_voice_name",
             _obj({"voice_name": {"type": "string", "enum": ["Fenrir", "Puck", "Charon", "Aoede"]}},
                  ("voice_name",)),
             risk=WRITE, behaviour=NON_BLOCKING, scheduling=WHEN_IDLE),

    # ---- DESTRUCTIVE: needs Haider's explicit yes ---------------------------
    ToolSpec("delete_task",
             "Permanently delete a task. Requires confirmation.",
             "tools:delete_task", _obj({"task_id": I}, ("task_id",)),
             risk=DESTRUCTIVE, confirmation="always", behaviour=BLOCKING),
]

BY_NAME: Dict[str, ToolSpec] = {s.name: s for s in SPECS}


# ---------------------------------------------------------------------------
# 4. Registration and declarations (this is what kills B6)
# ---------------------------------------------------------------------------

def register_all() -> int:
    """Push every spec into the database. Safe to run at every boot."""
    for s in SPECS:
        db.register_tool_spec(s.name, s.description, s.params, s.handler,
                              risk=s.risk, confirmation=s.confirmation,
                              behaviour=s.behaviour, scheduling=s.scheduling, domain=s.domain)
    return len(SPECS)


def declarations(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """Gemini-ready function declarations, generated from data. Never hand-write these."""
    return db.tool_declarations(domain)


def manifest() -> Dict[str, Any]:
    """G7: Jarvis describes itself from rows instead of improvising."""
    return db.capability_manifest()


def risk_of(name: str) -> str:
    s = BY_NAME.get(name)
    return s.risk if s else "unknown"


def needs_confirmation(name: str) -> bool:
    s = BY_NAME.get(name)
    return bool(s and s.confirmation == "always")


def behaviour_of(name: str) -> Tuple[str, str]:
    s = BY_NAME.get(name)
    return (s.behaviour, s.scheduling) if s else (BLOCKING, WHEN_IDLE)


# ---------------------------------------------------------------------------
# 5. Argument checking — the LLM proposes, code disposes
# ---------------------------------------------------------------------------

def _coerce(value: Any, kind: str) -> Any:
    """Voice models send '90' where a number belongs. Convert rather than crash."""
    if value is None:
        return None
    if kind == "integer":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            value = value.strip().replace(",", "")
            if value.lower() in ("", "none", "null"):
                return None
        return int(float(value))
    if kind == "number":
        if isinstance(value, str):
            value = value.strip()
            if value.lower() in ("", "none", "null"):
                return None
        return float(value)
    if kind == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "y", "1", "on")
        return bool(value)
    if kind == "string":
        return value if isinstance(value, str) else str(value)
    return value


def _validate(spec: ToolSpec, args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], Optional[str]]:
    """Returns (clean_args, warnings, error). Unknown keys are dropped, not fatal (R2)."""
    props = spec.params.get("properties", {})
    required = spec.params.get("required", [])
    clean: Dict[str, Any] = {}
    warns: List[str] = []

    if not isinstance(args, dict):
        return {}, [], "arguments must be an object"

    for k in args:
        if k not in props:
            warns.append(f"ignored unexpected argument '{k}'")

    for key, rule in props.items():
        if key not in args or args[key] is None:
            continue
        kind = rule.get("type", "string")
        try:
            val = _coerce(args[key], kind)
        except (TypeError, ValueError):
            return {}, warns, f"'{key}' should be {kind}, got {args[key]!r}"
        if val is None:
            continue
        if "enum" in rule and val not in rule["enum"]:
            return {}, warns, f"'{key}' must be one of {rule['enum']}, got {val!r}"
        if "minimum" in rule and val < rule["minimum"]:
            return {}, warns, f"'{key}' must be >= {rule['minimum']}"
        if "maximum" in rule and val > rule["maximum"]:
            return {}, warns, f"'{key}' must be <= {rule['maximum']}"
        clean[key] = val

    missing = [k for k in required if k not in clean]
    if missing:
        return {}, warns, f"missing required argument(s): {', '.join(missing)}"

    # Belt and braces: never pass something the handler cannot accept.
    fn = resolve_handler(spec.handler)
    if fn is not None:
        try:
            sig = inspect.signature(fn)
            if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                for k in list(clean):
                    if k not in sig.parameters:
                        clean.pop(k)
                        warns.append(f"handler does not accept '{k}' — dropped")
        except (TypeError, ValueError):
            pass
    return clean, warns, None


def resolve_handler(path: str) -> Optional[Callable]:
    """'database:remember' -> the actual function."""
    try:
        mod_name, fn_name = path.split(":")
        import importlib
        return getattr(importlib.import_module(mod_name), fn_name)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 6. Confirmation gate (P10) — destructive tools wait for your yes
# ---------------------------------------------------------------------------

_PENDING: Dict[str, Dict[str, Any]] = {}
CONFIRM_TTL_SECS = 180


def _ask_confirmation(spec: ToolSpec, args: Dict[str, Any], actor: str,
                      turn_id: Optional[int]) -> Dict[str, Any]:
    token = secrets.token_hex(4)
    _PENDING[token] = {"name": spec.name, "args": args, "actor": actor,
                       "turn_id": turn_id, "expires": time.time() + CONFIRM_TTL_SECS}
    db.audit("confirm_requested", "tool_call", None,
             after={"tool": spec.name, "args": args}, actor=actor, turn_id=turn_id)
    return {"ok": False, "status": "needs_confirmation", "tool": spec.name,
            "confirm_token": token, "args": args,
            "say": f"That deletes data. Say 'yes, delete it' to confirm {spec.name}."}


def confirm(token: str, actor: str = "haider") -> Dict[str, Any]:
    """Called when Haider says yes. Tokens expire after 3 minutes."""
    p = _PENDING.pop(token, None)
    if not p:
        return {"ok": False, "error": {"type": "unknown_token", "message": "Nothing is waiting on that confirmation."}}
    if time.time() > p["expires"]:
        return {"ok": False, "error": {"type": "expired", "message": "That confirmation expired. Ask again."}}
    return execute(p["name"], p["args"], actor=actor, turn_id=p["turn_id"], confirmed=True)


def cancel(token: str) -> Dict[str, Any]:
    _PENDING.pop(token, None)
    return {"ok": True, "status": "cancelled"}


# ---------------------------------------------------------------------------
# 7. The one door every call goes through
# ---------------------------------------------------------------------------

def execute(name: str, args: Optional[Dict[str, Any]] = None, *, actor: str = "jarvis",
            turn_id: Optional[int] = None, confirmed: bool = False) -> Dict[str, Any]:
    """
    Validate -> permission -> run -> audit. Never raises.
    Returns {ok, tool, data|error, latency_ms, warnings}.
    """
    started = time.perf_counter()
    args = args or {}

    def done(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["tool"] = name
        payload["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return payload

    spec = BY_NAME.get(name)
    if spec is None:
        return done({"ok": False, "error": {"type": "unknown_tool",
                                            "message": f"No tool named '{name}'.",
                                            "available": sorted(BY_NAME)}})

    clean, warns, err = _validate(spec, args)
    if err:
        return done({"ok": False, "error": {"type": "bad_arguments", "message": err}, "warnings": warns})

    if spec.confirmation == "always" and not confirmed:
        return done(_ask_confirmation(spec, clean, actor, turn_id))

    fn = resolve_handler(spec.handler)
    if fn is None:
        return done({"ok": False, "error": {"type": "no_handler",
                                            "message": f"Handler '{spec.handler}' is missing."}})

    try:
        data = fn(**clean)
    except Exception as exc:                      # R2: a tool error must never kill the session
        db.audit("tool_error", "tool_call", None,
                 after={"tool": name, "args": clean, "error": str(exc)}, actor=actor, turn_id=turn_id)
        return done({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)},
                     "warnings": warns})

    if spec.risk in (WRITE, DESTRUCTIVE):
        db.audit(f"tool:{name}", "tool_call", None,
                 after={"args": clean, "result": _brief(data)}, actor=actor, turn_id=turn_id)

    return done({"ok": True, "data": data, "warnings": warns,
                 "behaviour": spec.behaviour, "scheduling": spec.scheduling})


def _brief(data: Any, limit: int = 400) -> Any:
    """Keep the audit log readable — no megabyte dashboard dumps."""
    try:
        text = json.dumps(data, default=str)
    except Exception:
        return str(type(data))
    return data if len(text) <= limit else text[:limit] + "…"


async def execute_async(name: str, args: Optional[Dict[str, Any]] = None, **kw) -> Dict[str, Any]:
    """
    R3: SQLite work must not run on the audio loop. The live layer awaits this;
    the blocking work happens on a worker thread.
    """
    return await asyncio.to_thread(execute, name, args, **kw)


# ---------------------------------------------------------------------------
# 8. Drift alarm (the anti-B6 test)
# ---------------------------------------------------------------------------

def coverage_report() -> Dict[str, Any]:
    registered = {r["name"] for r in db.rows("SELECT name FROM tool_specs WHERE enabled=1")}
    declared = {d["name"] for d in declarations()}
    spec_names = set(BY_NAME)
    broken = [s.name for s in SPECS if resolve_handler(s.handler) is None]
    try:
        import tools as legacy
        legacy_names = set(legacy.TOOL_REGISTRY)
    except Exception:
        legacy_names = set()
    report = {
        "specs": len(SPECS),
        "registered_in_db": len(registered),
        "declared_to_gemini": len(declared),
        "missing_handlers": broken,
        "specs_not_registered": sorted(spec_names - registered),
        "registered_without_spec": sorted(registered - spec_names),
        "legacy_tools_without_spec": sorted(legacy_names - spec_names),
        "destructive_tools": sorted(s.name for s in SPECS if s.risk == DESTRUCTIVE),
        "unprotected_destructive": sorted(s.name for s in SPECS
                                          if s.risk == DESTRUCTIVE and s.confirmation != "always"),
    }
    report["ok"] = not (broken or report["specs_not_registered"] or report["registered_without_spec"]
                        or report["legacy_tools_without_spec"] or report["unprotected_destructive"]
                        or len(declared) != len(SPECS))
    return report


if __name__ == "__main__":
    db.init_db()
    register_all()
    print(json.dumps(coverage_report(), indent=2))
