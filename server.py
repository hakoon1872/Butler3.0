"""
JARVIS — server.py  (audit item #15: app + relay, env reading starts here)
=========================================================================
The body: serves the wizard and HUD v4, relays voice between browser and
Gemini Live over a WebSocket, and exposes the read/write APIs the HUD uses.

Auth (R8): set JARVIS_ACCESS_TOKEN in .env and every /api/* + /ws/* call must
carry it (header X-Jarvis-Token or ?token=). Without it the server runs in
LOCAL DEV mode and prints a warning — fine on your laptop, NOT for the cloud.
DEPLOY.md walks through setting the token + TLS on the always-on host.
"""

import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent / ".env")
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ACCESS_TOKEN = os.getenv("JARVIS_ACCESS_TOKEN", "").strip()

from database import (init_db, is_setup_complete, get_conn, now_pkt, migrate_legacy_v3,
                      get_working_state, rows, row, scalar, write, audit, backup, remember,
                      iso_utc, parse_ts, BACKUP_DIR)
import registry
import assembler
import calculators
import models as model_roster
import session_manager as sm

init_db()
# B4: rescue real v3 data at boot. No-op if no legacy DB sits next to butler.db,
# backs up first, and skips sources it already imported (fingerprint check).
try:
    _rescue = migrate_legacy_v3()
    if _rescue.get("migrated"):
        print(f"[jarvis] legacy data rescued from {_rescue.get('source')}: {_rescue.get('imported')}")
except Exception as _e:
    print(f"[jarvis] legacy migration skipped: {_e}")
registry.register_all()

app = FastAPI(title="Jarvis // Night Desk")
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
BOOT_TIME = time.time()

if API_KEY and sm.GENAI_OK:
    print(f"[jarvis] REAL mode — Gemini key present, SDK {sm.GENAI_VERSION}. Voice is armed.")
else:
    print("[jarvis] REHEARSAL mode — no GEMINI_API_KEY (or SDK missing). Local understudy, real tools.")
if not ACCESS_TOKEN:
    print("[jarvis] WARNING: local dev mode, no auth. Set JARVIS_ACCESS_TOKEN in .env before exposing beyond this machine.")

# ---------------- auth (R8) ----------------
def _authed(request: Request) -> bool:
    if not ACCESS_TOKEN:
        return True
    tok = request.headers.get("x-jarvis-token") or request.query_params.get("token") or ""
    return secrets.compare_digest(tok, ACCESS_TOKEN)

@app.middleware("http")
async def auth_gate(request: Request, call_next):
    p = request.url.path
    # 20 Nov: first-run wizard sends no token (setup.html has no lock screen).
    # Exempt /api/setup ONLY while setup is incomplete; once complete it is gated again.
    first_run_setup = p == "/api/setup" and not is_setup_complete()
    if p.startswith("/api/") and p not in ("/api/bootstrap", "/api/health") and not first_run_setup and not _authed(request):
        return JSONResponse({"error": "unauthorized", "hint": "Send X-Jarvis-Token header (see .env)."}, status_code=401)
    return await call_next(request)

@app.get("/api/bootstrap")
async def bootstrap():
    return {"authRequired": bool(ACCESS_TOKEN), "mode": "real" if (API_KEY and sm.GENAI_OK) else "rehearsal",
            "setupComplete": is_setup_complete()}

@app.get("/api/health")
async def health():
    return {"ok": True, "mode": "real" if (API_KEY and sm.GENAI_OK) else "rehearsal",
            "sdk": sm.GENAI_VERSION, "schema_version": scalar("SELECT MAX(version) FROM schema_migrations", default=0),
            "tools": len(registry.SPECS), "uptime_secs": round(time.time() - BOOT_TIME),
            "auth": bool(ACCESS_TOKEN)}

# ---------------- pages ----------------
@app.get("/")
async def serve_root():
    # 20 Nov fix: '/' is dynamic (wizard vs HUD). Without Cache-Control, browsers
    # heuristically cache it from Last-Modified, so the post-Initialize redirect
    # replayed a stale wizard from cache. no-store forces a fresh decision every hit.
    page = "hud.html" if is_setup_complete() else "setup.html"
    return FileResponse(STATIC_DIR / page, headers={"Cache-Control": "no-store"})

@app.get("/legacy")
async def serve_legacy():
    return FileResponse(STATIC_DIR / "dashboard.html")

@app.get("/api/manifest")
async def serve_manifest():
    return JSONResponse({"coverage": registry.coverage_report(), "manifest": registry.manifest()})

@app.get("/api/dashboard")
async def serve_dashboard():
    rescue = None
    r = row("SELECT value_json FROM capabilities WHERE key='legacy_migration'")
    if r:
        report = json.loads(r["value_json"]).get("last_report") or {}
        if report.get("migrated"):
            rescue = {"done": True, "source": Path(report.get("source", "")).name,
                      "imported": report.get("imported", {})}
    return JSONResponse({
        "now": now_pkt().strftime("%A %Y-%m-%d %H:%M PKT"),
        "grades": calculators.calculate_grade_margins(),
        "feasibility": calculators.grade_feasibility(),
        "gpa": calculators.gpa_projection(),
        "allocations": calculators.validate_allocations(),
        "today": assembler.today_picture(),
        "state": get_working_state(),
        "rescue": rescue,
        "tools": {"count": len(registry.SPECS)},
    })

@app.get("/api/prompt")
async def serve_prompt():
    return JSONResponse(assembler.assemble_wake_context())

# ---------------- setup wizard (unchanged contract) ----------------
@app.post("/api/setup")
async def run_setup(request: Request):
    data = await request.json()
    conn = get_conn()
    try:
        for table in ["profile","domains","courses","assessments","grading_scale",
                      "time_blocks","routine_anchors","goals","projects","people"]:
            conn.execute(f"DELETE FROM {table}")
        p = data.get("profile", {})
        conn.execute("""INSERT INTO profile
            (id,name,preferred_name,university,program,semester,timezone,current_gpa,target_gpa,created_at)
            VALUES (1,?,?,?,?,?,?,?,?,?)""",
            (p.get("name"), p.get("preferred_name"), p.get("university") or "", p.get("program") or "",
             p.get("semester"), p.get("timezone","Asia/Karachi"), p.get("current_gpa"),
             p.get("target_gpa",4.0), now_pkt().isoformat()))
        for d in data.get("domains", []):
            conn.execute("INSERT INTO domains (name,enabled) VALUES (?,?)", (d["name"], 1 if d.get("enabled") else 0))
        for c in data.get("courses", []):
            conn.execute("""INSERT INTO courses
                (code,name,instructor,credits,nature,priority_tier,time_share_pct,difficulty,study_protocol,risk_flags)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (c["code"], c["name"], c.get("instructor"), c.get("credits",3), c.get("nature"),
                 c.get("priority",2), c.get("time_share",10), c.get("difficulty",3),
                 c.get("protocol"), c.get("risk_flags")))
            for a in c.get("assessments", []):
                conn.execute("""INSERT INTO assessments
                    (course_code,label,type,max_marks,obtained,expected,status)
                    VALUES (?,?,?,?,?,?,?)""",
                    (c["code"], a.get("label"), a["type"], a["max"],
                     a.get("obtained",0), a.get("expected",0), a.get("status","Upcoming")))
        for g in data.get("grading_scale", []):
            conn.execute("INSERT INTO grading_scale (threshold,letter,points) VALUES (?,?,?)",
                         (g["threshold"], g["letter"], g["points"]))
        for b in data.get("time_blocks", []):
            conn.execute("""INSERT INTO time_blocks
                (day_of_week,start_time,end_time,type,location,course_focus,guardrail)
                VALUES (?,?,?,?,?,?,?)""",
                (b["day"], b["start"], b["end"], b.get("type"), b.get("location"), b.get("focus"), b.get("guardrail")))
        for r in data.get("routine_anchors", []):
            conn.execute("INSERT INTO routine_anchors (type,time) VALUES (?,?)", (r["type"], r["time"]))
        for g in data.get("goals", []):
            conn.execute("INSERT INTO goals (domain,title,why,metric,deadline,priority) VALUES (?,?,?,?,?,?)",
                         (g.get("domain"), g["title"], g.get("why"), g.get("metric"), g.get("deadline"), g.get("priority")))
        for pr in data.get("projects", []):
            conn.execute("INSERT INTO projects (name,description,deadline,status) VALUES (?,?,?,?)",
                         (pr["name"], pr.get("desc"), pr.get("deadline"), "active"))
        for pe in data.get("people", []):
            conn.execute("INSERT INTO people (name,relation,notes) VALUES (?,?,?)",
                         (pe["name"], pe.get("relation"), pe.get("notes")))
        for k, v in data.get("settings", {}).items():
            conn.execute("INSERT OR REPLACE INTO system_config (key,value) VALUES (?,?)", (k, str(v)))
        conn.execute("UPDATE system_config SET value='true' WHERE key='setup_complete'")
        conn.commit()
        audit("setup_wizard", "profile", 1, actor="haider")
        return JSONResponse({"status":"success"})
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()

# ---------------- undo ledger (30s) ----------------
_UNDO: Dict[str, Dict[str, Any]] = {}
def _stash_undo(kind: str, rid: int, prev: Dict[str, Any]) -> str:
    tok = secrets.token_hex(5)
    _UNDO[tok] = {"kind": kind, "id": rid, "prev": prev, "expires": time.time() + 30}
    return tok

@app.post("/api/undo/{token}")
async def undo(token: str):
    u = _UNDO.pop(token, None)
    if not u or time.time() > u["expires"]:
        return JSONResponse({"ok": False, "error": "undo expired"}, status_code=410)
    if u["kind"] == "task":
        write("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (u["prev"]["status"], iso_utc(), u["id"]))
    elif u["kind"] == "fact":
        write("UPDATE facts SET status='active', retired_at=NULL WHERE id=?", (u["id"],))
    elif u["kind"] == "assessment":
        write("UPDATE assessments SET obtained=?, expected=?, status=? WHERE id=?",
              (u["prev"]["obtained"], u["prev"]["expected"], u["prev"]["status"], u["id"]))
    audit(f"undo:{u['kind']}", u["kind"], u["id"], after=u["prev"], actor="haider")
    return {"ok": True, "restored": u["kind"], "id": u["id"]}

# ---------------- tasks CRUD ----------------
@app.get("/api/tasks")
async def list_tasks(q: str = "", status: str = "", course: str = "", sort: str = "deadline"):
    sql = """SELECT t.*, c.priority_tier, c.name AS course_name FROM tasks t
             LEFT JOIN courses c ON t.course_code=c.code WHERE 1=1"""
    args: list = []
    if q:
        sql += " AND (t.title LIKE ? OR t.course_code LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    if status:
        sql += " AND t.status=?"; args.append(status)
    else:
        sql += " AND t.status NOT IN ('cancelled','duplicate')"
    if course:
        sql += " AND t.course_code=?"; args.append(course)
    order = {"deadline": "(t.deadline_at IS NULL), t.deadline_at ASC, t.priority ASC, t.id ASC",
             "priority": "t.priority ASC, t.id ASC",
             "created": "t.id DESC",
             "course": "t.course_code ASC, t.id ASC"}.get(sort, "t.id DESC")
    sql += f" ORDER BY {order}"
    return {"tasks": rows(sql, args)}

@app.post("/api/tasks")
async def create_task(request: Request):
    d = await request.json()
    if not (d.get("title") or "").strip():
        return JSONResponse({"ok": False, "error": "title required"}, status_code=400)
    from database import resolve_deadline
    dl = resolve_deadline(d.get("deadline_hint"))
    tid = write("""INSERT INTO tasks (course_code,title,est_mins,status,due_date,deadline_at,
                   assigned_date,assigned_window,assigned_location,energy_need,location_need,priority,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ((d.get("course_code") or "").upper() or None, d["title"].strip(),
                 max(5, int(d.get("est_mins", 45))), "pending", d.get("deadline_hint"),
                 iso_utc(dl) if dl else None, d.get("assigned_date"), d.get("assigned_window"),
                 d.get("assigned_location"), int(d.get("energy_need", 3)), d.get("location_need"),
                 int(d.get("priority", 2)), iso_utc()))
    audit("hud:create_task", "tasks", tid, after=d, actor="haider")
    return {"ok": True, "id": tid, "task": row("SELECT * FROM tasks WHERE id=?", (tid,))}

@app.patch("/api/tasks/{tid}")
async def patch_task(tid: int, request: Request):
    d = await request.json()
    t = row("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    fields = {k: d[k] for k in ("title","course_code","est_mins","status","assigned_date","assigned_window",
                                "assigned_location","energy_need","location_need","priority") if k in d}
    if "deadline_hint" in d:
        from database import resolve_deadline
        dl = resolve_deadline(d["deadline_hint"])
        fields["deadline_at"] = iso_utc(dl) if dl else None
        fields["due_date"] = d["deadline_hint"]
    if fields.get("status") == "completed":
        fields["completed_at"] = iso_utc()
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        write(f"UPDATE tasks SET {sets}, updated_at=? WHERE id=?", (*fields.values(), iso_utc(), tid))
    audit("hud:patch_task", "tasks", tid, before=t, after=fields, actor="haider")
    return {"ok": True, "task": row("SELECT * FROM tasks WHERE id=?", (tid,))}

@app.delete("/api/tasks/{tid}")
async def delete_task(tid: int):
    t = row("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    write("UPDATE tasks SET status='cancelled', updated_at=? WHERE id=?", (iso_utc(), tid))
    audit("hud:delete_task", "tasks", tid, before=t, actor="haider")
    return {"ok": True, "undo_token": _stash_undo("task", tid, t)}

@app.post("/api/tasks/bulk")
async def bulk_tasks(request: Request):
    d = await request.json()
    ids = [int(i) for i in d.get("ids", [])]
    action = d.get("action")
    if action not in ("complete", "cancel") or not ids:
        return JSONResponse({"ok": False, "error": "bad bulk request"}, status_code=400)
    st = "completed" if action == "complete" else "cancelled"
    for tid in ids:
        write("UPDATE tasks SET status=?, updated_at=?, completed_at=? WHERE id=?",
              (st, iso_utc(), iso_utc() if st == "completed" else None, tid))
    audit(f"hud:bulk_{action}", "tasks", None, after={"ids": ids}, actor="haider")
    return {"ok": True, "count": len(ids)}

# ---------------- facts CRUD ----------------
@app.get("/api/facts")
async def list_facts(q: str = "", domain: str = "", status: str = "active"):
    sql = "SELECT * FROM facts WHERE 1=1"; args: list = []
    if status:
        sql += " AND status=?"; args.append(status)
    if q:
        sql += " AND (key LIKE ? OR value LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    if domain:
        sql += " AND domain=?"; args.append(domain)
    sql += " ORDER BY importance DESC, COALESCE(last_verified_at, created_at) DESC LIMIT 200"
    return {"facts": rows(sql, args)}

@app.post("/api/facts")
async def create_fact(request: Request):
    d = await request.json()
    if not (d.get("key") or "").strip() or not (d.get("value") or "").strip():
        return JSONResponse({"ok": False, "error": "key and value required"}, status_code=400)
    res = remember(d.get("domain", "general"), d["key"].strip(), d["value"].strip(),
                   source=d.get("source", "stated"), importance=int(d.get("importance", 3)),
                   sensitivity=d.get("sensitivity", "normal"), actor="haider")
    return {"ok": True, "fact": res}

@app.patch("/api/facts/{fid}")
async def patch_fact(fid: int, request: Request):
    d = await request.json()
    f = row("SELECT * FROM facts WHERE id=?", (fid,))
    if not f:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if "value" in d and d["value"] != f["value"]:
        # §5: nothing overwritten — supersede
        write("UPDATE facts SET status='retired', retired_at=? WHERE id=?", (iso_utc(), fid))
        res = remember(f["domain"], f["key"], d["value"], source="stated",
                       importance=int(d.get("importance", f["importance"])),
                       sensitivity=d.get("sensitivity", f["sensitivity"]), actor="haider")
        return {"ok": True, "superseded": fid, "fact": res}
    fields = {k: d[k] for k in ("importance", "sensitivity", "status") if k in d}
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        write(f"UPDATE facts SET {sets} WHERE id=?", (*fields.values(), fid))
    return {"ok": True, "fact": row("SELECT * FROM facts WHERE id=?", (fid,))}

@app.delete("/api/facts/{fid}")
async def delete_fact(fid: int):
    f = row("SELECT * FROM facts WHERE id=?", (fid,))
    if not f:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    write("UPDATE facts SET status='retired', retired_at=? WHERE id=?", (iso_utc(), fid))
    audit("hud:retire_fact", "facts", fid, before=f, actor="haider")
    return {"ok": True, "undo_token": _stash_undo("fact", fid, f)}

# ---------------- assessments / marks ----------------
@app.get("/api/assessments")
async def list_assessments(course: str = ""):
    if course:
        return {"assessments": rows("SELECT * FROM assessments WHERE course_code=? ORDER BY id", (course.upper(),))}
    return {"assessments": rows("SELECT * FROM assessments ORDER BY course_code, id")}

@app.post("/api/assessments")
async def record_mark(request: Request):
    d = await request.json()
    existing = row("SELECT * FROM assessments WHERE course_code=? AND label=?",
                   ((d.get("course_code") or "").upper(), d.get("label")))
    res = registry.execute("record_assessment", {
        "course_code": d.get("course_code", ""), "label": d.get("label", ""),
        "type_": d.get("type_", d.get("type", "Quiz")), "max_marks": float(d.get("max_marks", 0)),
        "obtained": float(d.get("obtained", 0)), "expected": float(d.get("expected", 0)),
        "status": d.get("status", "Upcoming")}, actor="haider")
    undo = None
    if existing:
        undo = _stash_undo("assessment", existing["id"], existing)
    return {"ok": res.get("ok", False), "result": res.get("data", res), "undo_token": undo}

# ---------------- commitments ----------------
@app.get("/api/commitments")
async def list_commitments():
    return {"commitments": rows("SELECT * FROM commitments ORDER BY status='open' DESC, due_at IS NULL, due_at LIMIT 100")}

@app.post("/api/commitments")
async def create_commitment(request: Request):
    d = await request.json()
    if not (d.get("promise") or "").strip():
        return JSONResponse({"ok": False, "error": "promise required"}, status_code=400)
    from database import resolve_deadline
    dl = resolve_deadline(d.get("due_hint"))
    cid = write("INSERT INTO commitments (domain,promise,made_at,due_at,status,ladder_level) VALUES (?,?,?,?, 'open',?)",
                (d.get("domain", "Academics"), d["promise"].strip(), iso_utc(),
                 iso_utc(dl) if dl else None, int(d.get("ladder_level", 2))))
    audit("hud:commitment", "commitments", cid, after=d, actor="haider")
    return {"ok": True, "id": cid}

@app.patch("/api/commitments/{cid}")
async def patch_commitment(cid: int, request: Request):
    d = await request.json()
    if d.get("status") not in ("kept", "broken", "renegotiated", "open"):
        return JSONResponse({"ok": False, "error": "bad status"}, status_code=400)
    write("UPDATE commitments SET status=?, closed_at=? WHERE id=?",
          (d["status"], iso_utc() if d["status"] != "open" else None, cid))
    audit("hud:commitment_outcome", "commitments", cid, after=d, actor="haider")
    return {"ok": True}

# ---------------- tools / confirm ----------------
@app.get("/api/tools")
async def list_tools():
    return {"tools": [{"name": s.name, "description": s.description, "params": s.params,
                       "risk": s.risk, "confirmation": s.confirmation,
                       "behaviour": s.behaviour, "scheduling": s.scheduling} for s in registry.SPECS],
            "coverage": registry.coverage_report()}

@app.post("/api/tools/run")
async def run_tool(request: Request):
    d = await request.json()
    res = registry.execute(d.get("name", ""), d.get("args", {}), actor="haider",
                           confirmed=bool(d.get("confirmed")))
    return JSONResponse(res)

@app.post("/api/confirm")
async def confirm_tool(request: Request):
    d = await request.json()
    if d.get("approve"):
        return JSONResponse(registry.confirm(d.get("token", ""), actor="haider"))
    return JSONResponse(registry.cancel(d.get("token", "")))

# ---------------- sessions / history ----------------
@app.get("/api/sessions")
async def list_sessions():
    return {"sessions": rows("""SELECT s.*, (SELECT COUNT(*) FROM turns t WHERE t.session_id=s.id) AS turns
                                FROM sessions s ORDER BY s.id DESC LIMIT 50""")}

@app.get("/api/sessions/{sid}/turns")
async def session_turns(sid: int):
    return {"turns": rows("SELECT * FROM turns WHERE session_id=? AND off_record=0 ORDER BY id", (sid,))}

# ---------------- settings ----------------
SETTINGS_WHITELIST = {"voice_name", "live_model", "text_model", "context_token_budget", "live_minute_cap",
                      "session_idle_close_secs", "quiet_start", "quiet_end", "nag_budget",
                      "accountability_academics", "accountability_other", "butler_name", "personality",
                      "wake_word_pref", "fact_reask_cap", "rollover_cap"}

@app.get("/api/settings")
async def get_settings():
    cfg = {r["key"]: r["value"] for r in rows("SELECT key,value FROM system_config")}
    return {"settings": {k: cfg.get(k) for k in SETTINGS_WHITELIST},
            "voices": ["Fenrir", "Puck", "Charon", "Kore", "Aoede", "Leda", "Orus", "Zephyr"]}

@app.patch("/api/settings")
async def patch_settings(request: Request):
    d = await request.json()
    changed = {}
    for k, v in d.items():
        if k in SETTINGS_WHITELIST:
            write("INSERT INTO system_config (key,value,updated_at) VALUES (?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                  (k, str(v), iso_utc()))
            changed[k] = str(v)
    audit("hud:settings", "system_config", None, after=changed, actor="haider")
    return {"ok": True, "changed": changed}

# ---------------- backups ----------------
@app.post("/api/backup")
async def make_backup():
    path = backup("manual")
    return {"ok": True, "file": Path(path).name}

@app.get("/api/backup/download")
async def download_backup(name: str):
    p = (BACKUP_DIR / name).resolve()
    if not str(p).startswith(str(BACKUP_DIR.resolve())) or not p.exists():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return FileResponse(p, filename=p.name)

@app.get("/api/backups")
async def list_backups():
    files = sorted(BACKUP_DIR.glob("*.db"), reverse=True)[:20] if BACKUP_DIR.exists() else []
    return {"backups": [{"name": f.name, "bytes": f.stat().st_size,
                         "mtime": f.stat().st_mtime} for f in files]}

# ---------------- model roster (the 404 cure) ----------------
@app.get("/api/models")
async def models_roster():
    cur_t = str(scalar("SELECT value FROM system_config WHERE key='text_model'", default="gemini-3.8"))
    cur_l = str(scalar("SELECT value FROM system_config WHERE key='live_model'", default="gemini-3.8-live"))
    if not API_KEY or not sm.GENAI_OK:
        return {"available": False, "current_text": cur_t, "current_live": cur_l,
                "reason": "No GEMINI_API_KEY in .env — rehearsal mode. Add the key to see your real model roster."}
    try:
        rec = await asyncio.to_thread(model_roster.resolve, API_KEY)
        return {"available": True, **rec}
    except Exception as e:
        return {"available": False, "current_text": cur_t, "current_live": cur_l, "reason": str(e)}

@app.post("/api/models/autofix")
async def models_autofix():
    if not API_KEY or not sm.GENAI_OK:
        return JSONResponse({"ok": False, "error": "No API key in .env yet — nothing to auto-pick. Add GEMINI_API_KEY first."}, status_code=400)
    try:
        rec = await asyncio.to_thread(model_roster.resolve, API_KEY)
        if not rec["text"] and not rec["live"]:
            return JSONResponse({"ok": False, "error": "No suitable text/live models found on this key.",
                                 "catalogue": rec["catalogue"]}, status_code=502)
        changed = await asyncio.to_thread(model_roster.apply, rec["text"], rec["live"])
        return {"ok": True, **changed, "recommended_text": rec["text"], "recommended_live": rec["live"]}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

# ---------------- capability probe (audit §10) ----------------
@app.post("/api/probe")
async def probe(request: Request):
    checks = []
    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    add("SDK installed", sm.GENAI_OK, f"google-genai {sm.GENAI_VERSION}")
    add("Database integrity", scalar("PRAGMA integrity_check") == "ok")
    cov = registry.coverage_report()
    add("Tool coverage", not cov.get("missing_handlers"), f"{cov.get('specs')} specs, {cov.get('declared_to_gemini')} declared")
    add("Wake assembler", assembler.assemble_wake_context()["est_tokens"] > 0)
    if not API_KEY:
        add("API key", False, "absent — rehearsal mode. Paste GEMINI_API_KEY into .env to arm the real voice.")
        report = {"mode": "rehearsal", "checks": checks}
        write("INSERT INTO capabilities (key,value_json,verified,probed_at) VALUES ('probe_report',?,0,?) "
              "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, probed_at=excluded.probed_at",
              (json.dumps(report), iso_utc()))
        return report
    from google import genai
    from google.genai import types as gt
    client = genai.Client(api_key=API_KEY)
    add("API key", True, "present")
    try:
        rec = await asyncio.to_thread(model_roster.resolve, API_KEY)
        add("Model roster", bool(rec["text"] or rec["live"]),
            f"{len(rec['catalogue'])} models on your key; suggested text={rec['text']}, live={rec['live']}")
    except Exception as e:
        add("Model roster", False, e)
    live_model = str(scalar("SELECT value FROM system_config WHERE key='live_model'", default="gemini-3.8-live"))
    text_model = str(scalar("SELECT value FROM system_config WHERE key='text_model'", default="gemini-3.8"))
    for mid in (live_model, text_model):
        try:
            m = await asyncio.to_thread(client.models.get, model=mid)
            add(f"Model {mid}", True, getattr(m, "display_name", "") or "reachable")
        except Exception as e:
            add(f"Model {mid}", False, e)
    try:
        t0 = time.perf_counter()
        r = await asyncio.to_thread(client.models.generate_content, model=text_model,
                                    contents="Reply with exactly: OK",
                                    config=gt.GenerateContentConfig(max_output_tokens=8))
        add("Text ping", bool(r.text), f"{round((time.perf_counter()-t0)*1000)} ms — {(r.text or '').strip()[:40]}")
    except Exception as e:
        add("Text ping", False, e)
    try:
        t0 = time.perf_counter()
        async with client.aio.live.connect(model=live_model,
                config=gt.LiveConnectConfig(response_modalities=["AUDIO"])) as s:
            await s.close()
        add("Live handshake", True, f"{round((time.perf_counter()-t0)*1000)} ms — session opened & closed cleanly")
    except Exception as e:
        add("Live handshake", False, e)
    try:
        r = await asyncio.to_thread(client.models.generate_content, model=text_model,
                                    contents="Jawab Roman Urdu mein do: theek hai",
                                    config=gt.GenerateContentConfig(max_output_tokens=24))
        add("Roman Urdu", bool(r.text), (r.text or "").strip()[:60])
    except Exception as e:
        add("Roman Urdu", False, e)
    report = {"mode": "real", "checks": checks}
    write("INSERT INTO capabilities (key,value_json,verified,probed_at) VALUES ('probe_report',?,1,?) "
          "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, verified=1, probed_at=excluded.probed_at",
          (json.dumps(report, default=str), iso_utc()))
    return report

# ---------------- the voice WebSocket (relay) ----------------
@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    if ACCESS_TOKEN:
        tok = ws.query_params.get("token") or ""
        if not secrets.compare_digest(tok, ACCESS_TOKEN):
            await ws.close(code=4401)
            return
    origin = ws.headers.get("origin")
    host = ws.headers.get("host", "")
    if origin and host and origin.split("://", 1)[-1] != host:
        await ws.close(code=4403)
        return
    out_q: asyncio.Queue = asyncio.Queue()

    async def sender():
        while True:
            item = await out_q.get()
            if isinstance(item, (bytes, bytearray)):
                await ws.send_bytes(item)
            else:
                await ws.send_text(json.dumps(item, default=str))

    async def emit(payload):
        await out_q.put(payload)

    session = sm.LiveSession(emit=emit, actor="haider", api_key=API_KEY or None)
    send_task = asyncio.create_task(sender())
    await session.start()
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"]:
                await session.push_audio(msg["bytes"])
            elif "text" in msg and msg["text"]:
                try:
                    d = json.loads(msg["text"])
                except Exception:
                    continue
                t = d.get("type")
                if t == "text":
                    asyncio.create_task(session.handle_text(d.get("text", ""), d.get("source", "typed"), bool(d.get("off_record"))))
                elif t == "ptt_start":
                    await session.activity(True)
                elif t == "ptt_stop":
                    await session.activity(False)
                elif t == "interrupt":
                    await session.interrupt()
                elif t == "confirm":
                    await session.resolve_confirm(d.get("id", ""), bool(d.get("approve")))
                elif t == "end":
                    break
                elif t == "ping":
                    await emit({"type": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await emit({"type": "error", "message": f"relay error: {exc}"})
    finally:
        await session.end("client_disconnect")
        send_task.cancel()
        try:
            await asyncio.wait_for(out_q.put({"type": "ended", "reason": session.close_reason}), timeout=1)
            await asyncio.wait_for(sender_drain(out_q, ws), timeout=1)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass

async def sender_drain(q: asyncio.Queue, ws: WebSocket):
    while not q.empty():
        item = q.get_nowait()
        if isinstance(item, (bytes, bytearray)):
            await ws.send_bytes(item)
        else:
            await ws.send_text(json.dumps(item, default=str))


@app.on_event("startup")
async def _prewarm_kokoro():
    import asyncio
    async def _warm():
        try:
            await asyncio.to_thread(_get_kokoro)
            print("[tts] Kokoro-82M pre-warmed in RAM (<0.15s latency ready).")
        except Exception as e:
            print(f"[tts] Kokoro pre-warm skipped: {e}")
    asyncio.create_task(_warm())


@app.get("/api/tts/voices")
async def api_tts_voices():
    return {
        "default": "af_heart",
        "voices": [
            {"id": "af_heart", "label": "Heart · Warm Uplifting (Female)"},
            {"id": "af_bella", "label": "Bella · Bright Upbeat (Female)"},
            {"id": "af_sky", "label": "Sky · Light Cute Airy (Female)"},
            {"id": "af_nicole", "label": "Nicole · Soft Friendly (Female)"},
            {"id": "af_nova", "label": "Nova · Clear Energetic (Female)"},
            {"id": "af_sarah", "label": "Sarah · Gentle Natural (Female)"},
            {"id": "bf_emma", "label": "Emma · Crisp British (Female)"},
            {"id": "bf_isabella", "label": "Isabella · Warm British (Female)"},
            {"id": "am_puck", "label": "Puck · Lively Youthful (Male)"},
            {"id": "am_fenrir", "label": "Fenrir · Confident Crisp (Male)"},
            {"id": "am_michael", "label": "Michael · Warm Natural (Male)"},
            {"id": "am_eric", "label": "Eric · Friendly Upbeat (Male)"},
            {"id": "bm_lewis", "label": "Lewis · Classic British Butler (Male)"},
            {"id": "bm_george", "label": "George · Deep British (Male)"},
        ],
    }


_KOKORO_ENGINE = None


def _get_kokoro():
    global _KOKORO_ENGINE
    if _KOKORO_ENGINE is None:
        from kokoro_onnx import Kokoro
        _KOKORO_ENGINE = Kokoro("models/kokoro/kokoro-v1.0.int8.onnx", "models/kokoro/voices-v1.0.bin")
    return _KOKORO_ENGINE


def _clean_for_speech(text: str) -> str:
    import re
    t = re.sub(r"```[\s\S]*?```", " code block omitted. ", text)
    t = re.sub(r"^\s*[-*•]+\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"[*_`#~<>|]+", "", t)
    t = t.replace("&", " and ").replace("→", " to ").replace("—", ", ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _synth_kokoro_wav(clean_text: str, voice: str) -> bytes:
    import io, soundfile as sf
    kokoro = _get_kokoro()
    samples, sr = kokoro.create(clean_text, voice=voice, speed=1.08, lang="en-us")
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV")
    return buf.getvalue()


@app.post("/api/tts")
async def api_tts(req: Request):
    import asyncio, io
    from fastapi.responses import Response
    body = await req.json()
    text = (body.get("text") or "").strip()
    voice = (body.get("voice") or "af_heart").strip()
    clean = _clean_for_speech(text)
    if not clean:
        return Response(content=b"", media_type="audio/wav")
    try:
        wav_bytes = await asyncio.to_thread(_synth_kokoro_wav, clean, voice)
        return Response(content=wav_bytes, media_type="audio/wav")
    except Exception as _tts_err:
        print(f"[tts] Kokoro fallback ({voice}): {_tts_err}")
        import edge_tts
        comm = edge_tts.Communicate(clean, "en-GB-SoniaNeural", rate="+6%")
        buf = io.BytesIO()
        async for ch in comm.stream():
            if ch["type"] == "audio":
                buf.write(ch["data"])
        return Response(content=buf.getvalue(), media_type="audio/mpeg")


@app.post("/api/stt")
async def api_stt(req: Request):
    """Unified brain voice-to-text using Gemini Flash audio understanding."""
    import os
    from fastapi import UploadFile
    form = await req.form()
    audio: UploadFile = form.get("audio")
    if not audio:
        return {"ok": False, "error": "No audio uploaded"}
    raw = await audio.read()
    if len(raw) < 400:
        return {"ok": True, "text": ""}
    mime = (audio.content_type or "audio/webm").split(";")[0].strip()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "GEMINI_API_KEY missing"}
    try:
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=api_key)
        prompt = (
            "Transcribe the user's speech verbatim into clean text. "
            "Context: Pakistani university student (COMSATS Lahore) discussing courses "
            "(AMA, POM, RTT, AUD, GBM, Pak Studies), schedules, tasks, and notes. "
            "Output ONLY the spoken words—no timestamps, no commentary, no quotes."
        )
        for m in ("gemini-2.5-flash-lite", "gemini-2.5-flash"):
            try:
                resp = await client.aio.models.generate_content(
                    model=m,
                    contents=[gt.Part.from_bytes(data=raw, mime_type=mime), prompt],
                )
                txt = (resp.text or "").strip()
                return {"ok": True, "text": txt}
            except Exception:
                continue
        return {"ok": False, "error": "STT model unavailable"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
