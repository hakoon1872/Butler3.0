"""
JARVIS — session_manager.py  (audit item #13: the live session state machine)
=============================================================================
"""

import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import database as db
import registry
import assembler
import calculators
import models

try:
    from google import genai
    from google.genai import types as gt
    GENAI_OK = True
    GENAI_VERSION = getattr(genai, "__version__", "?")
except Exception:                                    # pragma: no cover
    GENAI_OK = False
    GENAI_VERSION = None

STATES = ["IDLE", "CONNECTING", "LIVE", "GOAWAY", "RESUMING", "CLOSING"]

# 20 Nov (session 6): Live API STUBBED by owner decision (OD1 — Kokoro-only).
# One voice mode: mic → local Whisper STT → Gemini TEXT brain → text + Kokoro TTS.
# To re-arm the Live door later, set JARVIS_ENABLE_LIVE=1 in .env (needs the v1beta port).
import os as _os
LIVE_ENABLED = _os.getenv("JARVIS_ENABLE_LIVE", "0").strip() == "1"

PERSONA = (
    "You are Jarvis — a warm, gentle, uplifting, and natural personal companion. "
    "Speak naturally like a caring, sharp friend — warm, encouraging, and expressive, never stiff or robotic. "
    "Keep spoken replies concise and conversational; this is voice, not an essay. "
    "You understand English, Urdu and Roman Urdu; reply in the language you are addressed in. "
    "ARITHMETIC RULE: never improvise marks, dates or scheduling maths — call the calculator and "
    "planner tools and report their verdicts faithfully, even when the news is bad. "
    "Destructive actions require the user's explicit yes; the runtime enforces this. "
    "Course codes and names are context hints for voice recognition. Always use short course codes (AMA, POM, RTT, AUD, GBM, PAK) when calling task/grade tools, and never call add_task twice for the same task when the user merely reschedules or clarifies it. "
    "If state is marked STALE, ask one fresh check-in question before relying on it."
)


def _cfg(key: str, default: str) -> str:
    return str(db.scalar("SELECT value FROM system_config WHERE key=?", (key,), default=default))


class LiveSession:
    """One conversation. `emit` is an async callable receiving JSON-able dicts
    (control events) or bytes (PCM16 24kHz audio for the browser)."""

    def __init__(self, emit: Callable, actor: str = "haider", api_key: Optional[str] = None):
        self.emit = emit
        self.actor = actor
        self.api_key = api_key or None
        self.mode = "real" if (api_key and GENAI_OK) else "rehearsal"
        self.state = "IDLE"
        self.session_key = f"live-{uuid.uuid4().hex[:10]}"
        self.db_session_id: Optional[int] = None
        self.epoch = 0                      # bumps on each resumption
        self.turn_index = 0
        self.goaway_count = 0
        self.live_seconds = 0.0
        self.tokens_in = 0
        self.tokens_out = 0
        self.last_activity = time.monotonic()
        self.connected_at: Optional[float] = None
        self.close_reason: Optional[str] = None
        self._resumption_handle: Optional[str] = None
        self._sdk_session = None
        self._sdk_ctx = None
        self._client = None
        self._recv_task: Optional[asyncio.Task] = None
        self._timer_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()
        self._pending_confirms: Dict[str, Dict[str, Any]] = {}   # fc.id -> {token, fc, expires}
        self._user_buf: List[str] = []
        self._jarvis_buf: List[str] = []
        self._jarvis_committed = False
        self._user_committed = False
        self._text_turns = 0
        self._lock = asyncio.Lock()

    # ---------------- events & state ----------------
    async def _send(self, payload: Dict[str, Any]) -> None:
        try:
            await self.emit(payload)
        except Exception:
            pass                              # client gone; end() will follow

    async def _set_state(self, state: str, **extra) -> None:
        self.state = state
        await self._send({"type": "state", "state": state, "mode": self.mode, **extra})

    def _touch(self) -> None:
        self.last_activity = time.monotonic()

    # ---------------- ledger (R4: exactly once, every path) ----------------
    def _commit_turn(self, role: str, content: str, interrupted: bool = False,
                     tool_name: Optional[str] = None, suffix: str = "") -> None:
        content = (content or "").strip()
        if not content and not tool_name:
            return
        if not self.db_session_id:
            return
        self.turn_index += 1
        ctid = f"{self.epoch}:{self.turn_index}:{role}:{suffix}"
        db.write("""INSERT OR IGNORE INTO turns
                    (session_id, turn_index, role, content, ts_utc, interrupted, client_turn_id, tool_name)
                    VALUES (?,?,?,?,?,?,?,?)""",
                 (self.db_session_id, self.turn_index, role, content or f"[{tool_name}]",
                  db.iso_utc(), int(interrupted), ctid, tool_name))

    def _flush_partial(self, interrupted: bool = True) -> None:
        if self._user_buf and not self._user_committed:
            self._commit_turn("user", " ".join(self._user_buf), interrupted)
            self._user_committed = True
        if self._jarvis_buf and not self._jarvis_committed:
            self._commit_turn("jarvis", " ".join(self._jarvis_buf), interrupted)
            self._jarvis_committed = True
        self._user_buf, self._jarvis_buf = [], []

    def _new_turn_buffers(self) -> None:
        self._user_buf, self._jarvis_buf = [], []
        self._user_committed = self._jarvis_committed = False

    # ---------------- lifecycle ----------------
    async def start(self) -> None:
        self.db_session_id = db.write(
            "INSERT INTO sessions (session_key, channel, model, started_at, last_activity_at) VALUES (?,?,?,?,?)",
            (self.session_key, "voice", _cfg("live_model", "gemini-3.8-live") if self.mode == "real"
             else "rehearsal-understudy", db.iso_utc(), db.iso_utc()))
        await self._set_state("CONNECTING", mode=self.mode, session_key=self.session_key)
        if self.mode == "real":
            if not LIVE_ENABLED:
                # Single voice mode (OD1): never dial Gemini Live. Ears = local Whisper
                # (/api/stt), brain = Gemini text model (handle_text → text_stream),
                # voice = local Kokoro (/api/tts). push_audio is a safe no-op here.
                await self._set_state("LIVE", mode=self.mode,
                                      note="Single voice mode: local ears · one Gemini text brain · local Kokoro voice. Live API parked.")
                await self._send({"type": "transcript", "role": "system",
                                  "text": "Voice ready, sir — speak or type. Local ears, one brain, local voice."})
                self.connected_at = time.monotonic()
                self._timer_task = asyncio.create_task(self._watchdog())
                await self._send({"type": "hello", "mode": self.mode,
                                  "voice": _cfg("voice_name", "Aoede"),
                                  "cap_mins": int(_cfg("live_minute_cap", "90")),
                                  "idle_secs": int(_cfg("session_idle_close_secs", "120"))})
                return
            try:
                await self._connect(resume=False)
            except Exception as exc:
                healed = False
                if models.is_404(exc):
                    try:
                        rec = await models.resolve_async(self.api_key)
                        new_live = rec.get("live")
                        old_live = _cfg("live_model", "gemini-3.8-live")
                        if new_live and new_live != old_live:
                            await asyncio.to_thread(models.apply, None, new_live)
                            await self._send({"type": "transcript", "role": "system",
                                              "text": f"'{old_live}' isn't on your key, sir — I've switched the live "
                                                      f"model to '{new_live}' (saved in Settings). Reconnecting…"})
                            await self._connect(resume=False)
                            healed = True
                    except Exception:
                        healed = False
                if not healed:
                    await self._send({"type": "error", "message": f"Could not reach Gemini: {exc}",
                                      "hint": "Check GEMINI_API_KEY in .env; if the model 404s, open System → Settings → Auto-pick models."})
                    await self.end("connect_failed")
                    return
        else:
            await asyncio.sleep(0.15)
            await self._set_state("LIVE", mode=self.mode,
                                  note="Rehearsal: local understudy, real tools. Add GEMINI_API_KEY for the true voice.")
            await self._send({"type": "transcript", "role": "system",
                              "text": "Rehearsal mode — no API key yet. Every tool, confirmation and ledger entry below is real; "
                                      "only the thinking is local. Add your key to .env to wake the true Jarvis."})
        self.connected_at = time.monotonic()
        self._timer_task = asyncio.create_task(self._watchdog())
        await self._send({"type": "hello", "mode": self.mode,
                          "voice": _cfg("voice_name", "Aoede"),
                          "cap_mins": int(_cfg("live_minute_cap", "90")),
                          "idle_secs": int(_cfg("session_idle_close_secs", "120"))})

    async def _connect(self, resume: bool) -> None:
        cfg = gt.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=PERSONA + "\n\n# WAKE BRIEFING\n" + assembler.assemble_wake_context()["text"],
            speech_config=gt.SpeechConfig(
                voice_config=gt.VoiceConfig(
                    prebuilt_voice_config=gt.PrebuiltVoiceConfig(voice_name=_cfg("voice_name", "Aoede")))),
            tools=[gt.Tool(function_declarations=[
                gt.FunctionDeclaration(
                    name=s["name"], description=s["description"], parameters=s["parameters"],
                    behavior=getattr(gt.Behavior, registry.behaviour_of(s["name"])[0], gt.Behavior.BLOCKING))
                for s in registry.declarations()])],
            session_resumption=gt.SessionResumptionConfig(handle=self._resumption_handle if resume else None),
            context_window_compression=gt.ContextWindowCompressionConfig(
                trigger_tokens=20000, sliding_window=gt.SlidingWindow(target_tokens=12000)),
            input_audio_transcription=gt.AudioTranscriptionConfig(),
            output_audio_transcription=gt.AudioTranscriptionConfig(),
            realtime_input_config=gt.RealtimeInputConfig(
                automatic_activity_detection=gt.AutomaticActivityDetection(disabled=False)),
        )
        if self._client is None:
            self._client = genai.Client(
                api_key=self.api_key,
                http_options=gt.HttpOptions(api_version="v1beta")
            )
        if self._sdk_ctx is not None:
            try:
                await self._sdk_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._sdk_ctx = self._client.aio.live.connect(model=_cfg("live_model", "gemini-3.8-live"), config=cfg)
        self._sdk_session = await self._sdk_ctx.__aenter__()
        if self._recv_task:
            self._recv_task.cancel()
        self._recv_task = asyncio.create_task(self._receive_loop())
        await self._set_state("RESUMING" if resume else "LIVE", resumed=resume, epoch=self.epoch)

    async def _receive_loop(self) -> None:
        try:
            async for msg in self._sdk_session.receive():
                self._touch()
                await self._dispatch(msg)
                if self._closed.is_set():
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed.is_set():
                if "inactivity" in str(exc).lower() or "1008" in str(exc):
                    await self._send({"type": "transcript", "role": "system",
                                      "text": "Voice channel entered standby due to silence. Reconnect or tap Mic when ready to speak."})
                    await self.end("voice_standby")
                else:
                    await self._send({"type": "error", "message": f"Live stream error: {exc}"})
                    await self._try_resume(f"stream_error: {exc}")

    async def _dispatch(self, msg) -> None:
        if msg.setup_complete is not None:
            await self._set_state("LIVE", resumed=self.epoch > 0)
        if msg.session_resumption_update is not None:
            u = msg.session_resumption_update
            if u.resumable and u.new_handle:
                self._resumption_handle = u.new_handle
                db.write("UPDATE sessions SET resumption_handle=? WHERE id=?",
                         (u.new_handle, self.db_session_id))
        if msg.go_away is not None:
            self.goaway_count += 1
            db.write("UPDATE sessions SET goaway_count=? WHERE id=?", (self.goaway_count, self.db_session_id))
            await self._set_state("GOAWAY", time_left=str(getattr(msg.go_away, "time_left", "")))
            await self._try_resume("goaway")
            return
        if msg.tool_call is not None and msg.tool_call.function_calls:
            for fc in msg.tool_call.function_calls:
                asyncio.create_task(self._run_tool_call(fc))
        if msg.tool_call_cancellation is not None:
            for fid in (msg.tool_call_cancellation.ids or []):
                self._pending_confirms.pop(fid, None)
        if msg.usage_metadata is not None:
            self.tokens_in += getattr(msg.usage_metadata, "prompt_token_count", 0) or 0
            self.tokens_out += getattr(msg.usage_metadata, "response_token_count", 0) or 0
            await self._send({"type": "usage", "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                              "live_seconds": round(self.live_seconds)})
        sc = msg.server_content
        if sc is None:
            return
        if sc.input_transcription is not None and getattr(sc.input_transcription, "text", None):
            self._user_buf.append(sc.input_transcription.text)
            await self._send({"type": "transcript", "role": "user", "text": sc.input_transcription.text, "stream": True})
        if sc.output_transcription is not None and getattr(sc.output_transcription, "text", None):
            self._jarvis_buf.append(sc.output_transcription.text)
            await self._send({"type": "transcript", "role": "jarvis", "text": sc.output_transcription.text, "stream": True})
        if sc.interrupted:
            self._flush_partial(interrupted=True)
            self._new_turn_buffers()
            await self._send({"type": "interrupted"})
        if sc.model_turn is not None:
            for part in (sc.model_turn.parts or []):
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    await self.emit(part.inline_data.data)          # binary PCM16 24kHz
        status = getattr(sc, "interaction_status", None)
        if sc.turn_complete or sc.generation_complete:
            idle = status is None or str(status).endswith("IDLE")
            if self._user_buf and not self._user_committed:
                self._commit_turn("user", " ".join(self._user_buf))
                self._user_committed = True
            if idle and self._jarvis_buf and not self._jarvis_committed:
                self._commit_turn("jarvis", " ".join(self._jarvis_buf))
                self._jarvis_committed = True
                self._new_turn_buffers()
            await self._send({"type": "turn_complete", "idle": idle})

    async def _try_resume(self, reason: str) -> None:
        if self._closed.is_set() or self.mode != "real":
            return
        async with self._lock:
            self.epoch += 1
            self._flush_partial(interrupted=True)
            self._new_turn_buffers()
            for attempt in range(3):
                try:
                    await self._connect(resume=bool(self._resumption_handle))
                    await self._send({"type": "resumed", "reason": reason, "attempt": attempt + 1})
                    return
                except Exception as exc:
                    await self._send({"type": "error", "message": f"Resume attempt {attempt+1} failed: {exc}"})
                    await asyncio.sleep(1.5 * (attempt + 1))
            await self.end("resume_failed")

    # ---------------- tools (R3: off the audio thread) ----------------
    async def _run_tool_call(self, fc) -> None:
        name = fc.name or ""
        args = dict(fc.args or {})
        await self._send({"type": "tool_call", "id": fc.id, "name": name, "args": args})
        spec_behaviour, scheduling = registry.behaviour_of(name)
        try:
            result = await asyncio.to_thread(registry.execute, name, args, actor=self.actor)
        except Exception as exc:                       # R2: never kill the session
            result = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        if result.get("status") == "needs_confirmation":
            token = result.get("confirm_token")
            self._pending_confirms[fc.id] = {"token": token, "fc": fc, "expires": time.time() + 180}
            await self._send({"type": "confirm_request", "id": fc.id, "token": token, "tool": name,
                              "args": args, "say": result.get("say", "This changes data. Approve?"),
                              "expires_in": 180})
            asyncio.create_task(self._confirm_timeout(fc.id))
            return
        await self._send({"type": "tool_result", "id": fc.id, "name": name, "result": result})
        self._commit_turn("tool", json.dumps({"tool": name, "args": args, "ok": result.get("ok")})[:2000],
                          tool_name=name, suffix=str(fc.id))
        await self._respond_tool(fc, result, spec_behaviour, scheduling)

    async def _respond_tool(self, fc, result: Dict[str, Any], behaviour: str, scheduling: str) -> None:
        if self.mode != "real" or self._sdk_session is None:
            return
        resp = gt.FunctionResponse(id=fc.id, name=fc.name, response=_jsonable(result))
        if behaviour == "NON_BLOCKING":
            resp.scheduling = getattr(gt.FunctionResponseScheduling, scheduling,
                                      gt.FunctionResponseScheduling.WHEN_IDLE)
        try:
            await self._sdk_session.send_tool_response(function_responses=[resp])
        except Exception as exc:
            await self._send({"type": "error", "message": f"Tool response failed: {exc}"})

    async def _confirm_timeout(self, fc_id: str) -> None:
        await asyncio.sleep(181)
        p = self._pending_confirms.pop(fc_id, None)
        if p:
            await asyncio.to_thread(registry.cancel, p["token"])
            await self._respond_tool(p["fc"], {"ok": False, "status": "confirmation_expired"},
                                     *registry.behaviour_of(p["fc"].name or ""))
            await self._send({"type": "confirm_expired", "id": fc_id})

    async def resolve_confirm(self, fc_id: str, approve: bool) -> None:
        p = self._pending_confirms.pop(fc_id, None)
        if not p:
            await self._send({"type": "error", "message": "Nothing is waiting on that confirmation."})
            return
        if approve:
            result = await asyncio.to_thread(registry.confirm, p["token"], self.actor)
        else:
            result = await asyncio.to_thread(registry.cancel, p["token"])
            result = {"ok": True, "status": "cancelled_by_user"}
        await self._send({"type": "tool_result", "id": fc_id, "name": p["fc"].name, "result": result,
                          "confirmed": approve})
        await self._respond_tool(p["fc"], result, *registry.behaviour_of(p["fc"].name or ""))

    # ---------------- client inputs ----------------
    async def push_audio(self, pcm16: bytes) -> None:
        self._touch()
        if self.mode == "real" and self._sdk_session is not None and self.state == "LIVE":
            try:
                await self._sdk_session.send_realtime_input(
                    audio=gt.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))
            except Exception as exc:
                await self._send({"type": "error", "message": f"Mic send failed: {exc}"})

    async def activity(self, started: bool) -> None:
        self._touch()
        if self.mode == "real" and self._sdk_session is not None:
            try:
                if started:
                    await self._sdk_session.send_realtime_input(activity_start=gt.ActivityStart())
                else:
                    await self._sdk_session.send_realtime_input(activity_end=gt.ActivityEnd())
            except Exception:
                pass
        await self._send({"type": "activity", "started": started})

    async def interrupt(self) -> None:
        self._flush_partial(interrupted=True)
        self._new_turn_buffers()
        await self._send({"type": "interrupted"})

    async def handle_text(self, text: str, source: str = "typed", off_record: bool = False) -> None:
        """Typed text — separate path, never interrupts speech (R5/P7)."""
        self._touch()
        text = (text or "").strip()
        if not text:
            return
        self._text_turns += 1
        suffix = f"text{self._text_turns}"
        if not off_record:
            self._commit_turn("user", text, suffix=suffix)
        await self._send({"type": "transcript", "role": "user", "text": text, "source": source,
                          "off_record": off_record})
        await self._send({"type": "thinking", "path": "text"})
        reply_chunks: List[str] = []
        try:
            if self.mode == "real":
                import text_stream
                recent = db.rows(
                    "SELECT role, content FROM turns WHERE off_record=0 AND role IN ('user','jarvis') ORDER BY id DESC LIMIT 7"
                )
                hist = [{"role": r["role"], "text": r["content"]} for r in reversed(recent[1:])]
                async for chunk in text_stream.stream_reply(text, self.api_key, history=hist, off_record=off_record):
                    if chunk.get("tool_call"):
                        await self._send({"type": "tool_call", **chunk["tool_call"]})
                        res = chunk.get("result", {})
                        if res.get("status") == "needs_confirmation":
                            await self._send({"type": "confirm_request", "id": f"text-{self._text_turns}",
                                              "token": res["confirm_token"], "tool": chunk["tool_call"]["name"],
                                              "args": chunk["tool_call"].get("args", {}),
                                              "say": res.get("say", "Approve?"), "expires_in": 180,
                                              "text_path": True})
                        await self._send({"type": "tool_result", "name": chunk["tool_call"]["name"], "result": res})
                    if chunk.get("text"):
                        reply_chunks.append(chunk["text"])
                        await self._send({"type": "transcript", "role": "jarvis", "text": chunk["text"],
                                          "stream": True, "path": "text"})
            else:
                async for chunk in self._rehearsal_reply(text):
                    reply_chunks.append(chunk)
                    await self._send({"type": "transcript", "role": "jarvis", "text": chunk,
                                      "stream": True, "path": "text"})
        except Exception as exc:
            err = f"My apologies, sir — the text path stumbled: {exc}"
            reply_chunks.append(err)
            await self._send({"type": "transcript", "role": "jarvis", "text": err, "path": "text"})
        reply = "".join(reply_chunks).strip()
        if not off_record:
            self._commit_turn("jarvis", reply or "[no reply]", suffix=suffix)
        await self._send({"type": "turn_complete", "path": "text", "idle": True})

    # ---------------- rehearsal understudy (real tools, local intent) ----------------
    async def _rehearsal_reply(self, text: str):
        t = text.lower()
        codes = [c["code"] for c in db.rows("SELECT code FROM courses")]
        code = next((c for c in codes if c.lower() in t), codes[0] if codes else None)

        async def tool(name, args):
            tid = f"rh-{self._text_turns}-{name}"
            await self._send({"type": "tool_call", "id": tid, "name": name, "args": args})
            res = await asyncio.to_thread(registry.execute, name, args, actor=self.actor)
            if res.get("status") == "needs_confirmation":
                token = res.get("confirm_token")
                from types import SimpleNamespace
                fc = SimpleNamespace(id=tid, name=name)
                self._pending_confirms[tid] = {"token": token, "fc": fc, "expires": time.time() + 180}
                asyncio.create_task(self._confirm_timeout(tid))
                await self._send({"type": "confirm_request", "id": tid, "token": token, "tool": name,
                                  "args": args, "say": res.get("say", "Approve?"), "expires_in": 180})
                return "That deletes data, sir — I've placed the approval card in your transcript. Approve it and I'll proceed."
            await self._send({"type": "tool_result", "id": tid, "name": name, "result": res})
            if res.get("ok"):
                data = res.get("data")
                return {**(data if isinstance(data, dict) else {"result": data}), "ok": True}
            return {"ok": False, "say": (res.get("error") or {}).get("message", "that failed")}

        if any(w in t for w in ("hello", "hi ", "hey", "salam", "assalam")) or t.strip() in ("hi", "hey"):
            p = db.row("SELECT preferred_name FROM profile WHERE id=1") or {}
            yield f"Good day, {p.get('preferred_name') or 'sir'}. Jarvis at your service — in rehearsal, "
            yield "but every tool I call is the real one. Ask me about grades, the final, today's plan, or tell me to remember something."
            return
        if any(w in t for w in ("final", "need on", "required")) and code:
            letter = next((w.upper() for w in text.split() if w.upper() in
                           {s["letter"] for s in db.rows("SELECT letter FROM grading_scale")}), None)
            res = await tool("required_on_final", {"course_code": code, **({"target_letter": letter} if letter else {})})
            if isinstance(res, dict) and res.get("ok"):
                for tier in res["tiers"][:4]:
                    yield tier["say"] + " "
            else:
                yield (res.get("say") if isinstance(res, dict) else str(res))
            return
        if any(w in t for w in ("gpa",)):
            res = await tool("gpa_projection", {})
            yield res.get("say", "GPA is not configured yet, sir.") if isinstance(res, dict) else str(res)
            return
        if any(w in t for w in ("grade", "feasib", "possible", "marks", "how am i", "standing")):
            res = await tool("grade_feasibility", {"course_code": code} if code else {})
            if isinstance(res, dict) and res.get("ok"):
                for c in res["courses"]:
                    yield c["say"] + " "
            else:
                yield "No grades are configured yet — run the setup wizard first, sir."
            return
        if any(w in t for w in ("today", "plan", "schedule", "due", "task")):
            if any(w in t for w in ("add", "create", "new task")):
                title = text.split("task", 1)[-1].strip(" :,-") or "New task"
                res = await tool("add_task", {"title": title[:120], "course_code": code or "", "est_mins": 45})
                yield "Noted and logged, sir. " if isinstance(res, dict) and res.get("ok") else "I could not log that task. "
                return
            if "delete" in t:
                tid = next((w for w in t.split() if w.isdigit()), None)
                if tid:
                    msg = await tool("delete_task", {"task_id": int(tid)})
                    yield msg if isinstance(msg, str) else "Done, sir."
                    return
            pic = assembler.today_picture()
            yield f"Today is {pic['day']}. "
            if pic["today_tasks"]:
                yield "Scheduled: " + "; ".join(f"{x['title']} at {x.get('assigned_window') or 'an unslotted time'}" for x in pic["today_tasks"]) + ". "
            if pic["due_soon"]:
                yield "Due within 48 hours: " + "; ".join(x["title"] for x in pic["due_soon"]) + ". "
            if pic["unslotted_count"]:
                yield f"{pic['unslotted_count']} tasks still need slots — say the word and I'll find them."
            if not (pic["today_tasks"] or pic["due_soon"]):
                yield "A clear desk, sir. Rare and suspicious. Shall we fill it wisely?"
            return
        if any(w in t for w in ("remember", "note that", "don't forget")):
            val = text.split("that", 1)[-1].strip() if "that" in text else text
            res = await tool("remember", {"domain": "Academics", "key": f"note_{int(time.time())}", "value": val[:300]})
            yield "Committed to memory, sir. " if isinstance(res, dict) and res.get("ok") else "I could not store that. "
            return
        if any(w in t for w in ("tired", "energy", "stress", "check in", "check-in")):
            res = await tool("patch_working_state", {"energy": 2 if "tired" in t else 3})
            yield "Logged, sir. Pace yourself — the work will still be there after tea."
            return
        if any(w in t for w in ("tool", "can you do", "manifest", "capab")):
            names = [s.name for s in registry.SPECS]
            yield f"I carry {len(names)} tools, sir: " + ", ".join(names[:10]) + ", and more. The Tools panel lists every one."
            return
        yield "In rehearsal I answer from a small local script, sir — grades, finals, today's plan, memory, tasks and check-ins all work through the real tools. "
        yield "Place your GEMINI_API_KEY in .env and I'll think properly, with a voice. Until then: try “what do I need on the final for AMA?”"

    # ---------------- watchdog: cap + idle (cost guard, A5) ----------------
    async def _watchdog(self) -> None:
        cap = int(_cfg("live_minute_cap", "90")) * 60
        idle = int(_cfg("session_idle_close_secs", "120"))
        try:
            while not self._closed.is_set():
                await asyncio.sleep(2)
                if self.connected_at and self.state in ("LIVE", "GOAWAY", "RESUMING"):
                    self.live_seconds = time.monotonic() - self.connected_at
                    if self.mode == "real" and self.live_seconds > cap:
                        await self._send({"type": "cap_reached", "mins": cap // 60})
                        await self.end("minute_cap")
                        return
                    if time.monotonic() - self.last_activity > idle:
                        await self._send({"type": "idle_close", "secs": idle})
                        await self.end("idle")
                        return
                    await self._send({"type": "usage", "live_seconds": round(self.live_seconds),
                                      "tokens_in": self.tokens_in, "tokens_out": self.tokens_out})
        except asyncio.CancelledError:
            pass

    async def end(self, reason: str = "client_end") -> None:
        if self._closed.is_set():
            return
        self.close_reason = reason
        await self._set_state("CLOSING", reason=reason)
        self._flush_partial(interrupted=True)
        self._closed.set()
        if self._recv_task:
            self._recv_task.cancel()
        if self._timer_task:
            self._timer_task.cancel()
        if self._sdk_ctx is not None:
            try:
                await self._sdk_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        if self.db_session_id:
            db.write("""UPDATE sessions SET ended_at=?, last_activity_at=?, live_seconds=?,
                        goaway_count=?, close_reason=?, turn_count=? WHERE id=?""",
                     (db.iso_utc(), db.iso_utc(), round(self.live_seconds, 1), self.goaway_count,
                      reason, self.turn_index, self.db_session_id))
        await self._send({"type": "ended", "reason": reason, "live_seconds": round(self.live_seconds),
                          "turns": self.turn_index})


def _jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return json.loads(json.dumps(obj, default=str))
