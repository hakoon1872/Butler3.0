JARVIS v4 — item #12: academics end-to-end (M2 COMPLETE)
========================================================
WHAT'S NEW SINCE THE LAST ZIP (plain English)
  1. After setup you now get a REAL DASHBOARD instead of "Butler is Online":
     per-course grades with feasibility chips (green = secured, amber =
     reachable, red = unreachable), GPA cards (projected / floor / ceiling /
     target), today's blocks and tasks, what's due in 48h, and warnings.
  2. "Wake prompt" button (or /api/prompt): the EXACT briefing packet Jarvis
     will wake up with when voice arrives — identity, grade truth, today's
     plan, promises, capabilities, state, facts, last conversation. Under a
     token budget you can edit (system_config: context_token_budget).
  3. Change a mark -> the dashboard AND the wake prompt update immediately.
     No code edits. That was the milestone's official exit test. It passes.
  4. If your old data was rescued, a green banner says so on the dashboard.
     (Check it! It answers whether your 12 tasks came home.)
  Everything from the previous zip is still in here (self-healing DB,
  boot-time rescue, the accountant, 26 tools).

HOW TO INSTALL (2 minutes)
  1. Copy these into your Butler3.0 folder, REPLACING the old ones:
       database.py  tools.py  registry.py  server.py  calculators.py
       assembler.py  (NEW)
       static/setup.html  static/dashboard.html  (NEW)
       requirements.txt  .env.example
     AUDIT.md is included for reference — keep it with the project.
  2. KEEP jarvis_academic.db in the folder if your tasks haven't been
     rescued yet (the green banner will confirm when they are).
  3. No new installs. Same command:
       uvicorn server:app --host 127.0.0.1 --port 8000
  4. Open http://127.0.0.1:8000 -> wizard (first time) or the dashboard.

DO NOT copy exit_tests.py / exit_tests_12.py / seed_demo.py to your laptop —
sandbox-only scripts that wipe or fake databases on purpose.

NEXT MILESTONE (M3): the voice layer. Before it starts we make ONE decision
together: where Jarvis lives so he works without your laptop (cloud host).
.env with your AI Studio key starts being read at that point.
