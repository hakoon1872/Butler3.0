JARVIS v4 — Step A hardening + item #11 (the accountant)
========================================================
WHAT'S NEW (plain English)
  1. The crash you hit is fixed forever: if butler.db is deleted while the
     server runs, Jarvis quietly rebuilds his filing cabinets instead of
     throwing "Internal server error". (Deleting the DB still loses data —
     the backups/ folder is your safety net.)
  2. Your old data comes home automatically: if jarvis_academic.db (or
     butler_v3.db) sits in this folder, Jarvis backs it up and imports your
     tasks/history at startup. You'll see "[jarvis] legacy data rescued..."
     in the terminal. It never imports the same file twice.
  3. calculators.py — the accountant. Required-final marks, which grades are
     still possible, GPA projection, and a setup check against your
     15/10/25/50 policy. Jarvis now has 26 tools (was 22).
  4. Two old bugs fixed: banked marks no longer vanish unless a row says
     "Completed", cancelled work no longer inflates totals, and date hints
     like "tomorrow 9am" now mean 9am (not 23:59).

HOW TO INSTALL (2 minutes)
  1. Copy these files into your Butler3.0 folder, REPLACING the old ones:
       database.py  tools.py  registry.py  server.py  calculators.py
       static/setup.html  requirements.txt  .env.example
     (migrate_v3_to_v4.py is optional — the rescue now runs by itself.)
  2. KEEP your jarvis_academic.db in that folder — that's the data being rescued.
  3. No new installs needed. Same command as before:
       uvicorn server:app --host 127.0.0.1 --port 8000
  4. Open http://127.0.0.1:8000  → setup wizard (fresh start) or "Butler Online".
     Open http://127.0.0.1:8000/api/manifest  → see all 26 tools.

DO NOT copy exit_tests.py to your laptop — it wipes databases on purpose
(it exists only to prove the build works).

.env: copy .env.example, rename the copy to ".env", paste your key from
aistudio.google.com/apikey. Nothing reads it until the voice step — and
never share the key with anyone, including chats.
