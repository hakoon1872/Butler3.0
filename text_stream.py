"""
JARVIS — text_stream.py  (audit item #14: the split text path)
==============================================================
Preserves full model Part objects (including thought_signature) across tool rounds.
"""

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import database as db
import registry
import assembler
import models
from session_manager import PERSONA, _cfg

try:
    from google import genai
    from google.genai import types as gt
    GENAI_OK = True
except Exception:
    GENAI_OK = False

MAX_TOOL_ROUNDS = 4


async def _rounds(client, model: str, text: str, history: Optional[List[Dict[str, str]]],
                  briefing: str, decls: List[Any], off_record: bool = False) -> AsyncIterator[Dict[str, Any]]:
    contents: List[Any] = []
    for h in (history or [])[-6:]:
        contents.append(gt.Content(
            role="user" if h["role"] == "user" else "model",
            parts=[gt.Part(text=h["text"])]
        ))
    contents.append(gt.Content(role="user", parts=[gt.Part(text=text)]))
    config = gt.GenerateContentConfig(
        system_instruction=briefing,
        tools=(None if off_record else [gt.Tool(function_declarations=decls)]),
        max_output_tokens=1024,
    )

    for _round in range(MAX_TOOL_ROUNDS + 1):
        calls: List[Any] = []
        raw_model_parts: List[Any] = []
        round_has_text = False

        _models = list(dict.fromkeys([model, "gemini-2.5-flash", "gemini-2.5-flash-lite"]))
        _last_err = None
        for _m in _models:
            round_calls: List[Any] = []
            round_parts: List[Any] = []
            yielded_this_try = False
            try:
                async for chunk in await client.aio.models.generate_content_stream(
                    model=_m, contents=contents, config=config
                ):
                    if chunk.candidates and chunk.candidates[0].content:
                        for part in (chunk.candidates[0].content.parts or []):
                            round_parts.append(part)
                            if getattr(part, "text", None) and not getattr(part, "thought", False):
                                yielded_this_try = True
                                round_has_text = True
                                yield {"text": part.text}
                            if getattr(part, "function_call", None):
                                round_calls.append(part.function_call)
                calls = round_calls
                raw_model_parts = round_parts
                _last_err = None
                break
            except Exception as _e:
                _last_err = _e
                err_s = str(_e)
                if ("503" in err_s or "UNAVAILABLE" in err_s or "429" in err_s) and not yielded_this_try:
                    continue
                raise
        if _last_err:
            raise _last_err

        if not calls:
            if _round > 0 and not round_has_text:
                contents.append(gt.Content(
                    role="user",
                    parts=[gt.Part(text="Confirm briefly in 1-2 natural sentences what you just did or found.")]
                ))
                continue
            return

        contents.append(gt.Content(role="model", parts=raw_model_parts))

        responses = []
        for c in calls:
            res = await asyncio.to_thread(registry.execute, c.name, dict(c.args or {}), actor="haider")
            yield {"tool_call": {"name": c.name, "args": dict(c.args or {})}, "result": res}
            model_res = res
            if res.get("status") == "needs_confirmation":
                model_res = {
                    "ok": False,
                    "status": "awaiting_user_confirmation",
                    "note": "Destructive call parked for the user's approval in the dashboard.",
                }
            responses.append(gt.Part.from_function_response(
                name=c.name,
                response=json.loads(json.dumps(model_res, default=str)),
            ))
        contents.append(gt.Content(role="user", parts=responses))

    yield {"text": " (tool loop limit reached — ask me to continue, sir.)"}


async def stream_reply(text: str, api_key: str,
                       history: Optional[List[Dict[str, str]]] = None, off_record: bool = False) -> AsyncIterator[Dict[str, Any]]:
    if not GENAI_OK:
        yield {"text": "The Gemini SDK is not installed, sir — pip install -r requirements.txt."}
        return
    client = genai.Client(api_key=api_key)
    model = _cfg("text_model", "gemini-3.5-flash-lite")
    briefing = PERSONA + "\n\n# WAKE BRIEFING\n" + assembler.assemble_wake_context()["text"]
    decls = [gt.FunctionDeclaration(name=s["name"], description=s["description"], parameters=s["parameters"])
             for s in registry.declarations()]

    yielded_any = False
    tried_fallback = False

    while True:
        try:
            async for chunk in _rounds(client, model, text, history, briefing, decls, off_record=off_record):
                yielded_any = True
                yield chunk
            return
        except Exception as exc:
            if not tried_fallback and not yielded_any and (models.is_404(exc) or models.is_transient(exc)):
                tried_fallback = True
                try:
                    rec = await models.resolve_async(api_key)
                    candidates = [
                        m["id"] for m in rec["catalogue"]
                        if m["id"].startswith("gemini-3")
                        and "generateContent" in (m.get("actions") or [])
                        and not any(x in m["id"] for x in models.EXCLUDE)
                        and "live" not in m["id"]
                        and m["id"] != model
                    ]
                    new = rec.get("text") if models.is_404(exc) else (candidates[0] if candidates else None)
                    if new and new != model:
                        if models.is_404(exc):
                            await asyncio.to_thread(models.apply, new, None)
                        yield {"text": f"(Switching to '{new}'…) "}
                        model = new
                        continue
                except Exception:
                    pass
            yield {"text": f"My apologies, sir — the text path stumbled: {exc}"}
            return
