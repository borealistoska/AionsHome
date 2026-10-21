"""Persistent, bounded AI call diagnostics shared by background and chat calls."""

import asyncio
import inspect
import json
import logging
import re
import sqlite3
import time
import uuid
from functools import wraps
from contextlib import contextmanager

from fastapi import APIRouter
from config import BASE_DIR, DB_PATH

LOG_DB_PATH = DB_PATH.with_name("system_logs.db")
LIMIT = 30
log = logging.getLogger(__name__)
router = APIRouter()


@contextmanager
def _connect():
    db = sqlite3.connect(LOG_DB_PATH, timeout=5)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS logs (id TEXT PRIMARY KEY, started REAL, payload TEXT)")
            yield db
    finally:
        db.close()


def save_log(entry):
    with _connect() as db:
        db.execute("INSERT OR REPLACE INTO logs VALUES (?, ?, ?)",
                   (entry["log_id"], entry["started_at"], json.dumps(entry, ensure_ascii=False, default=str)))
        db.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY started DESC LIMIT ?)", (LIMIT,))


@router.get("/api/system-logs")
def list_logs():
    with _connect() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM logs ORDER BY started DESC LIMIT ?", (LIMIT,))]


@router.delete("/api/system-logs")
def clear_logs():
    with _connect() as db:
        db.execute("DELETE FROM logs")
    return {"ok": True}


def enrich_debug(data):
    """Attach the existing chat-only recall details to the underlying call."""
    log_id = data.get("log_id") or (data.get("usage") or {}).get("system_log_id")
    if not log_id:
        return data
    with _connect() as db:
        row = db.execute("SELECT payload FROM logs WHERE id = ?", (log_id,)).fetchone()
        if not row:
            return {**data, "log_id": log_id}
        entry = {**json.loads(row[0]), **data, "log_id": log_id}
        db.execute("UPDATE logs SET payload = ? WHERE id = ?",
                   (json.dumps(entry, ensure_ascii=False, default=str), log_id))
    return entry


async def _broadcast(entry):
    from ws import manager
    await manager.broadcast({"type": "system_log", "data": entry})


async def _publish(entry):
    # Diagnostics must never prevent the original AI call or mask its error.
    try:
        save_log(entry)
    except Exception:
        log.exception("Unable to save AI system log")
    try:
        await _broadcast(entry)
    except Exception:
        log.exception("Unable to broadcast AI system log")


def _new_entry(arguments, provider):
    cfg = arguments.get("cfg") or arguments.get("scfg") or arguments.get("model_cfg") or {}
    model = arguments.get("model") or cfg.get("model") or provider
    messages = arguments.get("messages")
    if messages is None:
        messages = [{"role": "user", "content": arguments.get("prompt") or arguments.get("text") or ""}]
    source = provider
    frame = inspect.currentframe()
    try:
        while frame:
            module = frame.f_globals.get("__name__", "")
            if module not in (__name__, "ai_providers") and (
                frame.f_code.co_filename.replace("\\", "/").startswith(str(BASE_DIR).replace("\\", "/"))
                or module.startswith("routes.") or module in ("camera", "schedule", "memory", "chatroom", "autonomy", "post_sentinel")
            ):
                source = f"{module}.{frame.f_code.co_name}"
                break
            frame = frame.f_back
    finally:
        del frame
    return {
        "log_id": uuid.uuid4().hex, "started_at": time.time(),
        "model": model, "provider": provider, "source": source,
        "status": "running", "has_error": False,
        "prompt_messages": [{"role": m.get("role", ""), "content": str(m.get("content", ""))} for m in messages],
        "prompt_count": len(messages), "usage": None,
    }


def logged_ai_call(provider, *, model=None):
    """Instrument provider boundaries, including callers that omit usage meta."""
    def decorate(func):
        signature = inspect.signature(func)

        def prepare(args, kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            entry = _new_entry(bound.arguments, provider)
            if model is not None:
                entry["model"] = model() if callable(model) else model
            meta = bound.arguments.get("meta")
            if "meta" in signature.parameters:
                if meta is None:
                    meta = {}
                    bound.arguments["meta"] = meta
                meta["system_log_id"] = entry["log_id"]
            return bound, entry, meta

        async def finish(entry, meta, status, error=""):
            entry.update(status=status, has_error=status in ("error", "cancelled"),
                         error_text=error or None, finished_at=time.time())
            entry["duration_seconds"] = round(entry["finished_at"] - entry["started_at"], 3)
            if meta is not None:
                entry["usage"] = {k: v for k, v in meta.items() if k not in ("system_log_id", "reasoning_content")} or None
            try:
                await _publish(entry)
            except asyncio.CancelledError:
                # Saving happens before broadcasting, even during cancellation.
                pass

        if inspect.isasyncgenfunction(func):
            @wraps(func)
            async def stream(*args, **kwargs):
                bound, entry, meta = prepare(args, kwargs)
                status, error = "success", ""
                generator = func(*bound.args, **bound.kwargs)
                try:
                    await _publish(entry)
                    async for chunk in generator:
                        if isinstance(chunk, str) and re.match(r"^\[[^\]\n]*(?:错误|error)[^\]\n]*\]", chunk, re.I):
                            status, error = "error", chunk
                        yield chunk
                except (asyncio.CancelledError, GeneratorExit):
                    status, error = "cancelled", "调用已中断"
                    raise
                except Exception as exc:
                    status, error = "error", f"{type(exc).__name__}: {exc}"
                    raise
                finally:
                    try:
                        await generator.aclose()
                    finally:
                        await finish(entry, meta, status, error)
            return stream

        @wraps(func)
        async def call(*args, **kwargs):
            bound, entry, meta = prepare(args, kwargs)
            # The Codex sentinel logs at its own boundary, also used directly.
            if (bound.arguments.get("scfg") or {}).get("provider") == "codex":
                return await func(*bound.args, **bound.kwargs)
            status, error = "success", ""
            try:
                await _publish(entry)
                result = await func(*bound.args, **bound.kwargs)
                if result is None:
                    status, error = "error", "模型未返回结果"
                return result
            except asyncio.CancelledError:
                status, error = "cancelled", "调用已中断"
                raise
            except Exception as exc:
                status, error = "error", f"{type(exc).__name__}: {exc}"
                raise
            finally:
                await finish(entry, meta, status, error)
        return call
    return decorate
