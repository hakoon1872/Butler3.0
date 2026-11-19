"""
JARVIS — calculators.py  (audit item #11: the accountant)
=========================================================
Plain English: this file answers the questions Jarvis used to improvise.

  * "What do I need on the final to get a B?"   -> required_on_final()
  * "Which grades are still possible?"          -> grade_feasibility()
  * "Are my marks set up correctly?"            -> validate_allocations()
  * "Where is my GPA heading?"                  -> gpa_projection()
  * "Marks so far, per course"                  -> calculate_grade_margins()
    (drop-in replacement for the tools.py version, with two bugs fixed)

Marks policy (confirmed by Haider, 16 Nov 2026): WEIGHTED POINTS.
  4 quizzes x 3.75 = 15 | 4 assignments x 2.5 = 10 | midterm 25 | final 50 = 100.
`max_marks` on an assessment IS its contribution to the course total.

Rules the maths follows (fixes the old engine's two bugs):
  * Marks you have earned count EVEN IF the row isn't marked "Completed"
    (the old engine made banked marks vanish).
  * "Cancelled" assessments are excluded from BOTH numerator and denominator
    (the old engine let them inflate the total). We flag the shrunken total
    instead of silently pretending it is still 100.
  * "Missed" work is settled at its obtained marks (usually 0): it stays in
    the denominator but offers no remaining potential.
  * "expected" marks are clamped to what is actually still winnable.

Everything here is READ-ONLY. No writes, no side effects, no API calls.
"""

from typing import Any, Dict, List, Optional

import database as db

# ---- the locked policy ------------------------------------------------------
MARKS_POLICY: Dict[str, Dict[str, float]] = {
    "Quiz":       {"count": 4, "each": 3.75, "total": 15.0},
    "Assignment": {"count": 4, "each": 2.5,  "total": 10.0},
    "Midterm":    {"count": 1, "each": 25.0, "total": 25.0},
    "Final":      {"count": 1, "each": 50.0, "total": 50.0},
}
COURSE_TOTAL = 100.0
EPS = 0.01                      # tolerance for float marks like 3.75
SETTLED = {"Completed", "Missed"}
OPEN = {"Upcoming", "In Progress"}


def _r2(x: float) -> float:
    return round(float(x), 2)


def _scale() -> List[Dict[str, Any]]:
    return db.rows("SELECT * FROM grading_scale ORDER BY threshold DESC")


def _no_scale() -> Dict[str, Any]:
    return {"ok": False,
            "say": "No grading scale is set yet, so letter grades are undefined. "
                   "Run the setup wizard (or add rows to grading_scale) first."}


def _grade_for(pct: float, scale: List[Dict[str, Any]]) -> Dict[str, Any]:
    for s in scale:
        if pct >= s["threshold"] - EPS:
            return {"letter": s["letter"], "gp": s["points"]}
    return {"letter": "F", "gp": 0.0}


# ---- the one place the maths happens ----------------------------------------
def _snapshot(course: Dict[str, Any], assessments: List[Dict[str, Any]],
              scale: List[Dict[str, Any]]) -> Dict[str, Any]:
    """All the numbers for one course, computed once, correctly."""
    live = [a for a in assessments if a["status"] != "Cancelled"]
    cancelled = [a for a in assessments if a["status"] == "Cancelled"]

    banked = 0.0            # marks already earned, whatever the status label
    remaining = 0.0         # marks still winnable
    expected_left = 0.0     # what Haider expects to earn from what's left
    effective_total = 0.0   # denominator: everything not cancelled
    settled_max = 0.0
    open_rows: List[Dict[str, Any]] = []
    final_row: Optional[Dict[str, Any]] = None

    for a in live:
        mx = max(0.0, float(a["max_marks"]))
        got = min(max(0.0, float(a["obtained"] or 0)), mx)
        effective_total += mx
        banked += got
        if a["status"] in SETTLED:
            settled_max += mx
            left = 0.0
        else:                                   # Upcoming / In Progress / anything else
            left = max(0.0, mx - got)
            exp = min(max(0.0, float(a["expected"] or 0)), left)
            expected_left += exp
            open_rows.append({**a, "_left": left, "_exp": exp})
            if str(a["type"]).lower() == "final" and (
                    final_row is None or left > final_row["_left"]):
                final_row = {**a, "_left": left, "_exp": exp}
        remaining += left

    floor_pct = (banked / effective_total * 100) if effective_total > 0 else 0.0
    projected_pct = ((banked + expected_left) / effective_total * 100) if effective_total > 0 else 0.0
    ceiling_pct = ((banked + remaining) / effective_total * 100) if effective_total > 0 else 0.0

    return {
        "course": course, "banked": banked, "remaining": remaining,
        "expected_left": expected_left, "effective_total": effective_total,
        "configured_total": effective_total + sum(float(a["max_marks"]) for a in cancelled),
        "cancelled": [a["label"] for a in cancelled],
        "settled_max": settled_max, "open_rows": open_rows, "final_row": final_row,
        "floor_pct": floor_pct, "projected_pct": projected_pct, "ceiling_pct": ceiling_pct,
        "floor_grade": _grade_for(floor_pct, scale),
        "projected_grade": _grade_for(projected_pct, scale),
        "ceiling_grade": _grade_for(ceiling_pct, scale),
    }


def _snapshots(course_code: Optional[str] = None):
    scale = _scale()
    if not scale:
        return None, None, _no_scale()
    if course_code:
        courses = db.rows("SELECT * FROM courses WHERE code=?", (course_code.upper().strip(),))
        if not courses:
            return None, None, {"ok": False,
                                "say": f"No course called '{course_code}'. Check the code and try again."}
    else:
        courses = db.rows("SELECT * FROM courses ORDER BY priority_tier ASC, time_share_pct DESC")
    snaps = []
    for c in courses:
        assessments = db.rows("SELECT * FROM assessments WHERE course_code=? ORDER BY id", (c["code"],))
        snaps.append(_snapshot(c, assessments, scale))
    return scale, snaps, None


def _tiers(snap: Dict[str, Any], scale: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per grade tier: secured / reachable / unreachable, and the price in marks."""
    out = []
    for s in scale:
        needed_total = s["threshold"] / 100.0 * snap["effective_total"]
        gap = needed_total - snap["banked"]
        if gap <= EPS:
            verdict, req, req_pct = "secured", 0.0, 0.0
        elif gap > snap["remaining"] + EPS:
            verdict, req, req_pct = "unreachable", _r2(gap), None
        else:
            verdict = "reachable"
            req = _r2(gap)
            req_pct = _r2(gap / snap["remaining"] * 100) if snap["remaining"] > 0 else None
        out.append({"letter": s["letter"], "threshold": s["threshold"], "points": s["points"],
                    "verdict": verdict, "required_from_remaining": req,
                    "required_pct_of_remaining": req_pct})
    return out


def _headline(code: str, tiers: List[Dict[str, Any]]) -> str:
    secured = [t["letter"] for t in tiers if t["verdict"] == "secured"]
    reachable = [t for t in tiers if t["verdict"] == "reachable"]
    dead = [t["letter"] for t in tiers if t["verdict"] == "unreachable"]
    bits = []
    if reachable:
        best = reachable[0]  # scale is high->low, so first reachable is the best
        bits.append(f"best still possible is {best['letter']} "
                    f"(needs {best['required_pct_of_remaining']}% of everything left)")
    if secured:
        bits.append(f"{secured[0]} is already secured even with zeros from here")
    if dead:
        bits.append(f"out of reach: {', '.join(dead)}")
    return f"{code}: " + "; ".join(bits) + "."


def _total_warning(snap: Dict[str, Any]) -> Optional[str]:
    if abs(snap["effective_total"] - COURSE_TOTAL) > EPS:
        return (f"course total is {_r2(snap['effective_total'])}, not 100 "
                f"(cancelled: {', '.join(snap['cancelled']) or 'none'}; "
                f"configured: {_r2(snap['configured_total'])}). "
                "Percentages are computed on the live total — confirm reweighting with the instructor.")
    return None


# ---- public tools (all read-only) --------------------------------------------
def calculate_grade_margins() -> Any:
    """Per-course marks so far, projection and letter grade. FIXED engine:
    banked marks always count; cancelled work never inflates the total.
    Keeps the old output keys so nothing downstream breaks."""
    scale, snaps, err = _snapshots()
    if err:
        return err
    results = []
    for sn in snaps:
        c = sn["course"]
        results.append({
            "code": c["code"], "name": c["name"], "instructor": c["instructor"],
            "credits": c["credits"], "tier": c["priority_tier"], "time_share_pct": c["time_share_pct"],
            "obtained_sum": _r2(sn["banked"]), "expected_sum": _r2(sn["expected_left"]),
            "max_so_far": _r2(sn["settled_max"]), "max_total": _r2(sn["effective_total"]),
            "projected_total": _r2(sn["banked"] + sn["expected_left"]),
            "projected_pct": _r2(sn["projected_pct"]),
            "projected_grade": sn["projected_grade"]["letter"], "projected_gp": sn["projected_grade"]["gp"],
            "study_protocol": c["study_protocol"], "risk_flags": c["risk_flags"],
            # new, honest extras:
            "floor_pct": _r2(sn["floor_pct"]), "floor_grade": sn["floor_grade"]["letter"],
            "ceiling_pct": _r2(sn["ceiling_pct"]), "ceiling_grade": sn["ceiling_grade"]["letter"],
            "remaining_potential": _r2(sn["remaining"]),
            "cancelled": sn["cancelled"], "warning": _total_warning(sn),
        })
    return results


def grade_feasibility(course_code: Optional[str] = None) -> Dict[str, Any]:
    """The truth table: for one course (or all), which grades are secured,
    still reachable, or mathematically dead — with the price in marks."""
    scale, snaps, err = _snapshots(course_code)
    if err:
        return err
    courses = []
    for sn in snaps:
        tiers = _tiers(sn, scale)
        courses.append({
            "code": sn["course"]["code"], "name": sn["course"]["name"],
            "banked": _r2(sn["banked"]), "remaining_potential": _r2(sn["remaining"]),
            "effective_total": _r2(sn["effective_total"]),
            "tiers": tiers, "say": _headline(sn["course"]["code"], tiers),
            "warning": _total_warning(sn),
        })
    return {"ok": True, "courses": courses}


def required_on_final(course_code: str, target_letter: Optional[str] = None) -> Dict[str, Any]:
    """Spell out the final-exam mark needed for each grade tier (or one target).
    Assumption stated openly: other pending work scores your 'expected' marks."""
    scale, snaps, err = _snapshots(course_code)
    if err:
        return err
    sn = snaps[0]
    fin = sn["final_row"]
    if fin is None:
        settled_final = db.row(
            "SELECT * FROM assessments WHERE course_code=? AND LOWER(type)='final' AND status!='Cancelled'",
            (sn["course"]["code"],))
        if settled_final:
            return {"ok": True, "course_code": sn["course"]["code"],
                    "say": f"The final is already settled: {settled_final['obtained']} out of "
                           f"{settled_final['max_marks']}. Use grade_feasibility for the full picture.",
                    "final_settled": True}
        return {"ok": False, "say": f"{sn['course']['code']} has no final-exam row. Add it first."}

    others_expected = sum(r["_exp"] for r in sn["open_rows"] if r["id"] != fin["id"])
    tiers_out = []
    for s in scale:
        if target_letter and s["letter"].lower() != target_letter.strip().lower():
            continue
        needed_total = s["threshold"] / 100.0 * sn["effective_total"]
        need = needed_total - sn["banked"] - others_expected
        if need <= EPS:
            verdict = "secured"
            say = (f"{s['letter']} is secured even with 0 on the final "
                   f"(if the other pending work hits your expected marks).")
            need = 0.0
        elif need > fin["_left"] + EPS:
            verdict = "unreachable"
            say = (f"{s['letter']} needs {_r2(need)} on the final but only {_r2(fin['_left'])} "
                   f"is winnable there — unreachable without salvaging other work.")
        else:
            verdict = "reachable"
            say = (f"{s['letter']} needs {_r2(need)} out of {_r2(fin['max_marks'])} on the final "
                   f"({_r2(need / fin['_left'] * 100)}% of it), assuming other pending work "
                   f"hits your expected marks.")
        tiers_out.append({"letter": s["letter"], "threshold": s["threshold"],
                          "required_on_final": _r2(need), "verdict": verdict, "say": say})
    if target_letter and not tiers_out:
        return {"ok": False, "say": f"'{target_letter}' is not a letter in your grading scale. "
                                    f"Known letters: {', '.join(s['letter'] for s in scale)}."}
    return {"ok": True, "course_code": sn["course"]["code"],
            "final_label": fin["label"], "final_max": _r2(fin["max_marks"]),
            "final_winnable": _r2(fin["_left"]),
            "assumption": "other pending assessments score their expected marks",
            "tiers": tiers_out, "warning": _total_warning(sn)}


def validate_allocations() -> Dict[str, Any]:
    """Typo insurance: does every course's assessment setup match the locked
    policy (4x3.75 quizzes, 4x2.5 assignments, 25 mid, 50 final = 100)?
    Catches the wizard's default-100 mistake before it poisons GPA maths."""
    courses = db.rows("SELECT * FROM courses ORDER BY code")
    if not courses:
        return {"ok": False, "say": "No courses configured yet."}
    report, all_ok = [], True
    for c in courses:
        assessments = db.rows("SELECT * FROM assessments WHERE course_code=? ORDER BY id", (c["code"],))
        live = [a for a in assessments if a["status"] != "Cancelled"]
        issues: List[str] = []
        total = sum(float(a["max_marks"]) for a in live)
        if abs(total - COURSE_TOTAL) > EPS:
            issues.append(f"weights sum to {_r2(total)}, should be 100.")
        for typ, pol in MARKS_POLICY.items():
            group = [a for a in live if str(a["type"]).lower() == typ.lower()]
            if len(group) != int(pol["count"]):
                issues.append(f"{len(group)} {typ} rows, policy says {int(pol['count'])}.")
            for a in group:
                if abs(float(a["max_marks"]) - pol["each"]) > EPS:
                    issues.append(f"'{a['label']}' is worth {_r2(a['max_marks'])} points, "
                                  f"policy says {pol['each']}.")
        for a in live:
            if float(a["obtained"] or 0) > float(a["max_marks"]) + EPS:
                issues.append(f"'{a['label']}': obtained {_r2(a['obtained'])} exceeds its max "
                              f"{_r2(a['max_marks'])}.")
        unknown = [a for a in live if str(a["type"]).lower() not in
                   {t.lower() for t in MARKS_POLICY}]
        for a in unknown:
            issues.append(f"'{a['label']}' has type '{a['type']}' — outside the policy "
                          f"(Quiz/Assignment/Midterm/Final).")
        cancelled = [a["label"] for a in assessments if a["status"] == "Cancelled"]
        if cancelled:
            issues.append(f"cancelled rows excluded from the total: {', '.join(cancelled)} "
                          f"— confirm with the instructor how those points are reweighted.")
        ok = not issues
        all_ok = all_ok and ok
        report.append({"code": c["code"], "name": c["name"], "ok": ok,
                       "live_total": _r2(total), "issues": issues,
                       "say": (f"{c['code']}: setup matches the 15/10/25/50 policy." if ok
                               else f"{c['code']}: " + " ".join(issues))})
    return {"ok": True, "all_courses_valid": all_ok, "policy": MARKS_POLICY, "courses": report,
            "say": ("Every course matches the marks policy." if all_ok else
                    "Some courses need fixing — see issues. Grades computed on them may be wrong.")}


def gpa_projection() -> Dict[str, Any]:
    """Credit-weighted GPA: where you're heading (expected), your floor
    (zeros from here) and your ceiling (perfect finish)."""
    scale, snaps, err = _snapshots()
    if err:
        return err
    if not snaps:
        return {"ok": False, "say": "No courses configured yet."}
    tot_credits = sum(float(sn["course"]["credits"] or 0) for sn in snaps)
    if tot_credits <= 0:
        return {"ok": False, "say": "Courses have no credits set, so GPA is undefined."}

    def gpa(key: str) -> float:
        return sum(float(sn["course"]["credits"] or 0) * sn[key]["gp"] for sn in snaps) / tot_credits

    profile = db.row("SELECT target_gpa, current_gpa FROM profile WHERE id=1") or {}
    projected = gpa("projected_grade")
    target = profile.get("target_gpa")
    out = {
        "ok": True,
        "projected_gpa": _r2(projected), "floor_gpa": _r2(gpa("floor_grade")),
        "ceiling_gpa": _r2(gpa("ceiling_grade")),
        "credits_counted": _r2(tot_credits),
        "courses": [{"code": sn["course"]["code"], "credits": sn["course"]["credits"],
                     "projected_grade": sn["projected_grade"]["letter"],
                     "projected_gp": sn["projected_grade"]["gp"],
                     "ceiling_grade": sn["ceiling_grade"]["letter"]} for sn in snaps],
        "target_gpa": target, "current_gpa": profile.get("current_gpa"),
    }
    if target is not None:
        gap = float(target) - projected
        out["say"] = (f"Projected GPA {_r2(projected)} vs target {target}: "
                      + (f"on track, {_r2(-gap)} above target." if gap <= 0 else
                         f"{_r2(gap)} short. Ceiling with a perfect finish: {out['ceiling_gpa']}."))
    return out
