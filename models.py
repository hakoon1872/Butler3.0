"""
JARVIS — models.py  (the roster checker)
========================================
"""
import asyncio
import re
from typing import Any, Dict, List, Optional
import database as db

TEXT_METHOD = "generateContent"
LIVE_METHOD = "bidiGenerateContent"
EXCLUDE = (
    "image", "embedding", "aqa", "tts", "computer-use", "imagen",
    "veo", "gemma", "learnlm", "gemini-2.0", "gemini-2.5", "transcribe", "translate"
)


def is_404(exc: BaseException) -> bool:
    s = str(exc)
    return "404" in s or "NOT_FOUND" in s or "no longer available" in s


def is_transient(exc: BaseException) -> bool:
    s = str(exc)
    return "503" in s or "UNAVAILABLE" in s or "429" in s or "RESOURCE_EXHAUSTED" in s


def catalogue(api_key: str) -> List[Dict[str, Any]]:
    from google import genai
    client = genai.Client(api_key=api_key)
    out: List[Dict[str, Any]] = []
    for m in client.models.list():
        mid = (m.name or "").split("/")[-1]
        actions = list(getattr(m, "supported_actions", None)
                       or getattr(m, "supported_generation_methods", None) or [])
        out.append({
            "id": mid,
            "display_name": m.display_name or mid,
            "actions": actions,
            "input_token_limit": getattr(m, "input_token_limit", None),
            "output_token_limit": getattr(m, "output_token_limit", None),
        })
    return out


def _version(mid: str) -> float:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", mid)
    return float(m.group(1)) if m else 0.0


def pick(cat: List[Dict[str, Any]], method: str, current: str, kind: str) -> Optional[str]:
    cands = []
    for m in cat:
        mid = m["id"]
        if not mid.startswith("gemini") or any(x in mid for x in EXCLUDE):
            continue
        if method not in (m.get("actions") or []):
            continue
        if kind == "text" and "live" in mid:
            continue
        if kind == "live" and "live" not in mid:
            continue
        cands.append(m)
    if not cands:
        return None

    def score(m: Dict[str, Any]):
        mid = m["id"]
        ver = _version(mid)
        if kind == "text":
            # Prefer flash-lite (500 RPD on free tier) over flash (20 RPD), then newest 3.x
            is_lite = 1 if ("flash" in mid and "lite" in mid) else 0
            is_flash = 1 if "flash" in mid else 0
            return (is_lite, is_flash, ver)
        else:
            # For Live API (unlimited RPD), prefer newest 3.x live without extended-thinking overhead
            is_base_live = 0 if "thinking" in mid else 1
            return (ver, is_base_live)

    cands.sort(key=lambda m: (score(m), m["id"]), reverse=True)
    return cands[0]["id"]


def resolve(api_key: Optional[str] = None, cat: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    current_text = str(db.scalar("SELECT value FROM system_config WHERE key='text_model'", default="gemini-3.5-flash-lite"))
    current_live = str(db.scalar("SELECT value FROM system_config WHERE key='live_model'", default="gemini-3.8-live"))
    if cat is None:
        if not api_key:
            raise ValueError("no API key")
        cat = catalogue(api_key)
    return {
        "text": pick(cat, TEXT_METHOD, current_text, "text"),
        "live": pick(cat, LIVE_METHOD, current_live, "live"),
        "current_text": current_text,
        "current_live": current_live,
        "catalogue": cat,
    }


def apply(text_id: Optional[str], live_id: Optional[str]) -> Dict[str, str]:
    changed = {}
    for key, val in (("text_model", text_id), ("live_model", live_id)):
        if val:
            db.write("""INSERT INTO system_config (key,value,updated_at) VALUES (?,?,?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                     (key, val, db.iso_utc()))
            changed[key] = val
    if changed:
        db.audit("models:autofix", "system_config", None, after=changed, actor="jarvis")
    return changed


async def resolve_async(api_key: str) -> Dict[str, Any]:
    return await asyncio.to_thread(resolve, api_key)
