"""
diagnose_live.py — JARVIS read-only "Live door" inspector  (session 3, 16 Nov 2026)
==================================================================================
PURPOSE: find out WHY the voice WebSocket gets HTTP 404 at handshake.
It tries four doors to Gemini, one by one, and prints a verdict:

  1. SDK door      — exactly what Jarvis uses (google-genai client.aio.live.connect)
  2. Raw v1beta    — the CURRENT official Live endpoint, dialed by hand
  3. Raw v1alpha   — the older Live endpoint, dialed by hand
  4. REST roster   — the "letter slot" (models.list) as a key/network sanity check

SAFETY: 100% read-only. No database access, no file writes, no settings changes.
Your API key is read from .env and is NEVER printed (any accidental echo is masked).

HOW TO RUN (on your laptop, in the Butler3.0 folder, venv activated):
    python diagnose_live.py
    python diagnose_live.py gemini-3.8-live     (optional: override the model id)
Then paste the WHOLE output back into the chat. It contains no secrets.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_key() -> str:
    """Read GEMINI_API_KEY from .env next to this script (or the environment)."""
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.getenv("GEMINI_API_KEY", "").strip()


KEY = load_key()
MODEL = sys.argv[1].strip() if len(sys.argv) > 1 else "gemini-3.8-live"


def safe(text: str) -> str:
    """Mask the key if it ever sneaks into an error message."""
    text = str(text)
    if KEY:
        text = text.replace(KEY, "***KEY-MASKED***")
    return text[:400]


def banner(title: str) -> None:
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


async def try_sdk_door():
    """Door 1: the exact path Jarvis uses in session_manager.py (L210/L216)."""
    from google import genai
    from google.genai import types as gt
    client = genai.Client(api_key=KEY)
    t0 = time.perf_counter()
    async with client.aio.live.connect(
            model=MODEL,
            config=gt.LiveConnectConfig(response_modalities=["AUDIO"])) as session:
        await session.close()
    return True, f"session opened & closed cleanly in {round((time.perf_counter()-t0)*1000)} ms"


async def try_raw_door(version: str):
    """Doors 2/3: dial the raw WebSocket endpoint by hand, send the setup message."""
    import websockets
    uri = (f"wss://generativelanguage.googleapis.com/ws/"
           f"google.ai.generativelanguage.{version}.GenerativeService.BidiGenerateContent?key={KEY}")
    t0 = time.perf_counter()
    async with websockets.connect(uri, open_timeout=15) as ws:
        handshake_ms = round((time.perf_counter() - t0) * 1000)
        await ws.send(json.dumps({"setup": {"model": f"models/{MODEL}"}}))
        try:
            first = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(first, (bytes, bytearray)):
                first = first.decode("utf-8", "replace")
            snippet = str(first)[:200]
        except asyncio.TimeoutError:
            snippet = "(handshake OK; no first message within 10s)"
        return True, f"handshake OK in {handshake_ms} ms; first reply: {snippet}"


def try_rest_roster():
    """Door 4: REST models.list — proves the key itself is alive."""
    from google import genai
    client = genai.Client(api_key=KEY)
    names = [getattr(m, "name", "?") for m in client.models.list()]
    return True, f"{len(names)} models visible on this key; e.g. {', '.join(names[:3])}"


async def run_test(label, coro_factory):
    print(f"\n--- {label} ---")
    try:
        ok, detail = await coro_factory()
        print(f"PASS  {safe(detail)}")
        return True
    except Exception as exc:
        name = type(exc).__name__
        status = getattr(getattr(exc, "response", None), "status_code", None)
        extra = f" (HTTP {status})" if status else ""
        print(f"FAIL  {name}{extra}: {safe(exc)}")
        return False


async def main():
    banner("JARVIS LIVE-DOOR DIAGNOSIS  (read-only, key never printed)")
    print(f"python      : {sys.version.split()[0]}")
    try:
        import google.genai as g
        print(f"google-genai: {getattr(g, '__version__', 'unknown')}")
    except Exception as e:
        print(f"google-genai: NOT IMPORTABLE — {safe(e)}")
    try:
        import websockets
        print(f"websockets  : {getattr(websockets, '__version__', 'unknown')}")
    except Exception as e:
        print(f"websockets  : NOT IMPORTABLE — {safe(e)}")
    print(f"API key     : {'present (' + str(len(KEY)) + ' chars)' if KEY else 'MISSING — put GEMINI_API_KEY in .env'}")
    print(f"Live model  : {MODEL}")
    if not KEY:
        print("\nVERDICT: no key found. Copy .env.example to .env and paste your key there (never in chat).")
        return

    sdk_ok = await run_test("DOOR 1 — SDK live connect (the path Jarvis uses)", try_sdk_door)
    beta_ok = await run_test("DOOR 2 — raw WebSocket, v1beta (current official endpoint)",
                             lambda: try_raw_door("v1beta"))
    alpha_ok = await run_test("DOOR 3 — raw WebSocket, v1alpha (older endpoint)",
                              lambda: try_raw_door("v1alpha"))
    rest_ok = await run_test("DOOR 4 — REST models.list (key/network sanity)",
                             lambda: asyncio.to_thread(try_rest_roster))

    banner("VERDICT")
    if sdk_ok:
        print("The SDK door OPENS. Voice should work now — restart Jarvis and talk.")
        print("If the HUD still shows an error, paste its exact text; the problem is elsewhere.")
    elif beta_ok:
        print("SDK door 404s, but the CURRENT (v1beta) door opens by hand.")
        print("=> The installed google-genai dials an outdated address.")
        print("FIX: pip install -U google-genai   (in the venv), restart Jarvis, talk.")
        print("Then run: pip show google-genai   and paste the Version line so we can re-pin requirements.txt.")
    elif alpha_ok:
        print("Only the OLDER (v1alpha) door opens.")
        print("=> Next agent instance must force api_version='v1alpha' on the live client")
        print("   (session_manager.py L210: genai.Client(..., http_options=...)). Paste this whole output.")
    elif rest_ok:
        print("ALL Live doors 404, but REST works => key/project or network restriction on the")
        print("Live endpoint specifically (region/firewall/proxy). Paste this whole output.")
    else:
        print("NOTHING opens, not even REST => check the key in .env (no quotes/spaces),")
        print("internet connection, VPN/firewall. Paste this whole output.")
    print("\nDone. No files or settings were changed. Paste everything above back into the chat.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted — nothing was changed.")
