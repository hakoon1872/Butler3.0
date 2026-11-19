import json
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from database import init_db, is_setup_complete, get_conn, now_pkt, migrate_legacy_v3
import registry

init_db()
# B4: rescue real v3 data at boot. No-op if no legacy DB sits next to butler.db,
# backs up first, and skips sources it already imported (fingerprint check).
try:
    _rescue = migrate_legacy_v3()
    if _rescue.get("migrated"):
        print(f"[jarvis] legacy data rescued from {_rescue.get('source')}: {_rescue.get('imported')}")
except Exception as _e:  # a bad legacy file must never block boot
    print(f"[jarvis] legacy migration skipped: {_e}")
registry.register_all()   # tool specs live in the database; keep them in sync at every boot
app = FastAPI(title="Butler // Life OS")
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_root():
    if not is_setup_complete():
        return FileResponse(STATIC_DIR / "setup.html")
    return HTMLResponse("""
    <html><head><title>Butler Online</title></head>
    <body style="background:#070b12;color:#38bdf8;font-family:system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;">
    <h1 style="font-size:48px;margin-bottom:10px;">🎩</h1>
    <h1>Butler is Online.</h1>
    <p style="color:#94a3b8;">Setup complete. Next step: wiring the AI voice layer.</p>
    </body></html>""")

@app.get("/api/manifest")
async def serve_manifest():
    """Read-only: what Jarvis can actually do, straight from the tool specs."""
    return JSONResponse({"coverage": registry.coverage_report(), "manifest": registry.manifest()})

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
            (p.get("name"), p.get("preferred_name"), p.get("university"), p.get("program"),
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
        return JSONResponse({"status":"success"})
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()
