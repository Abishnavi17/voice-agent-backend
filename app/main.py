"""
main.py - the entry point of the whole backend.

Stage 6 is the same app as Stage 5, with one Docker-friendly addition: on
startup we RETRY the database connection for a few seconds. In Docker the app
container can boot before Postgres is ready to accept connections, so a single
attempt could fail; retrying makes `docker compose up` reliable.

Full flow (unchanged):
    audio -> Whisper -> load history -> check Redis
          (hit: cached reply | miss: LLM then cache) -> save turn -> respond
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import generate_reply
from app.cache import get_cached_reply, set_cached_reply
from app.database import Conversation, SessionLocal, Turn, init_db
from app.transcription import transcribe_audio


async def _init_db_with_retry(attempts: int = 10, delay: float = 2.0) -> None:
    """Try to create tables, retrying so the app can wait for Postgres to be up.

    In Docker, the database may still be starting when the app boots. Rather than
    crash on the first failed connection, we retry a handful of times. This is a
    common, expected pattern in containerised apps.
    """
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            await init_db()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(delay)
    # If it still failed after all retries, raise so the problem is visible.
    raise RuntimeError(f"database not reachable after retries: {last_error}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _init_db_with_retry()
    yield


app = FastAPI(
    title="Voice Agent Backend",
    description="Async backend: speech -> text -> AI reply, with memory, caching, storage.",
    version="1.0.0",
    lifespan=lifespan,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "voice-agent-backend", "docs": "visit /docs"}


@app.post("/chat")
async def chat(
    audio: UploadFile = File(...),
    conversation_id: int = Form(0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Voice-agent endpoint: transcribe -> memory -> cache/LLM -> persist."""
    if audio.filename is None:
        raise HTTPException(status_code=400, detail="No file was uploaded.")

    suffix = Path(audio.filename).suffix or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    try:
        shutil.copyfileobj(audio.file, tmp)
        tmp.close()
        result = transcribe_audio(tmp_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    user_text = str(result["text"])

    if conversation_id and conversation_id > 0:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id} not found")
    else:
        conversation = Conversation()
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

    stmt = select(Turn).where(Turn.conversation_id == conversation.id).order_by(Turn.id)
    prior = (await db.execute(stmt)).scalars().all()
    history = [{"user": t.user_text, "agent": t.agent_text} for t in prior]
    history.append({"user": user_text, "agent": ""})

    context = "\n".join(f"U:{h['user']}|A:{h['agent']}" for h in history)
    cached = await get_cached_reply(context)

    if cached is not None:
        reply = cached
        was_cached = True
    else:
        try:
            reply = generate_reply(history)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Reply generation failed: {exc}") from exc
        await set_cached_reply(context, reply)
        was_cached = False

    turn = Turn(conversation_id=conversation.id, user_text=user_text, agent_text=reply)
    db.add(turn)
    await db.commit()
    await db.refresh(turn)

    return {
        "conversation_id": conversation.id,
        "turn_id": turn.id,
        "transcript": user_text,
        "reply": reply,
        "cached": was_cached,
        "language": result["language"],
        "duration_seconds": result["duration_seconds"],
    }


@app.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Return a whole conversation's turns in order."""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    stmt = select(Turn).where(Turn.conversation_id == conversation_id).order_by(Turn.id)
    turns = (await db.execute(stmt)).scalars().all()
    return {
        "conversation_id": conversation_id,
        "created_at": conversation.created_at.isoformat(),
        "turns": [
            {
                "turn_id": t.id,
                "user": t.user_text,
                "agent": t.agent_text,
                "created_at": t.created_at.isoformat(),
            }
            for t in turns
        ],
    }
