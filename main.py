"""MoodBreak server: FastAPI + two websockets.

/ws/app     — main client (start, mid-run answers, feedback turns)
/ws/viewer  — second screen: RAW tool_call / tool_result event stream, unprocessed
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agents import Runner  # noqa: E402
from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from agents_def import build_orchestrator  # noqa: E402
from tools import SessionCtx, now_ms, to_jsonable  # noqa: E402

ROOT = Path(__file__).parent
GEN = ROOT / "generated"
GEN.mkdir(exist_ok=True)

app = FastAPI(title="MoodBreak")

viewers: set[WebSocket] = set()
sessions: dict[str, dict] = {}  # sid -> {"ctx": SessionCtx, "history": list}


async def broadcast_raw(payload: dict) -> None:
    dead = []
    msg = json.dumps(payload, ensure_ascii=False, default=str)
    for ws in viewers:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        viewers.discard(ws)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/stream")
async def stream_page() -> FileResponse:
    return FileResponse(ROOT / "static" / "stream.html")


@app.websocket("/ws/viewer")
async def ws_viewer(ws: WebSocket) -> None:
    await ws.accept()
    viewers.add(ws)
    try:
        while True:
            await ws.receive_text()  # viewers don't send; keepalive only
    except WebSocketDisconnect:
        viewers.discard(ws)


async def run_turn(sid: str, input_items: list, send) -> None:
    """One orchestrator run (initial or feedback turn), streaming raw events."""
    sess = sessions[sid]
    ctx: SessionCtx = sess["ctx"]
    orchestrator = build_orchestrator()
    result = Runner.run_streamed(
        orchestrator, input=input_items, context=ctx, max_turns=60
    )
    async for event in result.stream_events():
        if event.type == "run_item_stream_event":
            raw = {
                "t": now_ms(),
                "stream": "run_item",
                "name": event.name,
                "item_type": event.item.type,
                "agent": getattr(event.item, "agent", None) and event.item.agent.name,
                "raw_item": to_jsonable(getattr(event.item, "raw_item", None)),
            }
            await broadcast_raw(raw)
            # narrate agent messages to the main client
            if event.item.type == "message_output_item":
                try:
                    from agents import ItemHelpers

                    text = ItemHelpers.text_message_output(event.item)
                except Exception:
                    text = ""
                if text:
                    await send({"type": "agent_text", "agent": raw["agent"], "text": text})
            elif event.item.type == "tool_call_item":
                ri = raw["raw_item"] or {}
                await send({
                    "type": "phase",
                    "tool": ri.get("name") if isinstance(ri, dict) else None,
                    "status": "called",
                })
            elif event.item.type == "tool_call_output_item":
                await send({"type": "phase", "tool": None, "status": "returned"})
        elif event.type == "agent_updated_stream_event":
            await broadcast_raw({
                "t": now_ms(),
                "stream": "agent_updated",
                "agent": event.new_agent.name,
            })
    sess["history"] = result.to_input_list()
    await send({"type": "done", "final": result.final_output})


@app.websocket("/ws/app")
async def ws_app(ws: WebSocket) -> None:
    await ws.accept()
    sid = uuid.uuid4().hex[:8]
    out_dir = GEN / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    async def send(payload: dict) -> None:
        await ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))

    ctx = SessionCtx(session_id=sid, out_dir=out_dir, send=send, broadcast_raw=broadcast_raw)
    sessions[sid] = {"ctx": ctx, "history": []}
    await send({"type": "session", "sid": sid})
    task: asyncio.Task | None = None

    try:
        while True:
            msg = json.loads(await ws.receive_text())
            mtype = msg.get("type")

            if mtype == "start":
                if task and not task.done():
                    await send({"type": "error", "message": "run already in progress"})
                    continue
                content: list = [{"type": "input_text", "text": _brief_text(msg)}]
                if msg.get("image_b64"):
                    content.append({
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{msg['image_b64']}",
                    })
                items = [{"role": "user", "content": content}]
                task = asyncio.create_task(_guarded(run_turn(sid, items, send), send))

            elif mtype == "user_response":
                # answer for present_mood_cards / ask_user
                if not ctx.resolve_user(msg.get("payload")):
                    await send({"type": "error", "message": "nothing waiting for user input"})

            elif mtype == "feedback":
                if task and not task.done():
                    await send({"type": "error", "message": "run already in progress"})
                    continue
                items = sessions[sid]["history"] + [
                    {"role": "user",
                     "content": [{"type": "input_text",
                                  "text": f"[FEEDBACK TURN] {msg.get('text','')}\n"
                                          "Remember: impact analysis first, re-invoke only affected axes."}]}
                ]
                task = asyncio.create_task(_guarded(run_turn(sid, items, send), send))
    except WebSocketDisconnect:
        if task and not task.done():
            task.cancel()
        sessions.pop(sid, None)


_LINEAGE_POOL = [
    "editorial print", "brutalist web", "retro OS/terminal", "Japanese minimal",
    "Swiss grid", "analog zine", "art deco signage", "1970s technical manual",
    "risograph poster", "luxury lookbook", "newspaper broadsheet", "cassette-era packaging",
]


def _brief_text(msg: dict) -> str:
    import random

    seeds = random.sample(_LINEAGE_POOL, 2)
    parts = [f"[PRODUCT BRIEF] {msg.get('brief', '')}"]
    parts.append(
        f"[VARIATION SEED] weak starting-point hints for this run only: {seeds[0]}, {seeds[1]}. "
        "User signals and product fit ALWAYS override these; use them only to avoid "
        "defaulting to the same lineages every run."
    )
    if msg.get("reference"):
        parts.append(f"[USER MOOD/REFERENCE SIGNALS] {msg['reference']}")
    if msg.get("image_b64"):
        parts.append("[A reference image is attached — analyze its mood and let it inform direction.]")
    return "\n".join(parts)


async def _guarded(coro, send) -> None:
    try:
        await coro
    except Exception as e:  # surface errors to the demo screen instead of dying silently
        await send({"type": "error", "message": f"{type(e).__name__}: {e}"})
        await broadcast_raw({"t": now_ms(), "stream": "error", "error": str(e)})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)
