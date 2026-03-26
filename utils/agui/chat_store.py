"""
Chat persistence — save/load conversations and messages to PostgreSQL (asyncpg).
Uses the polycode schema in finespresso_db.
"""

import json
import logging
from typing import Optional
from uuid import UUID, uuid4

from db.connection import get_pool

logger = logging.getLogger(__name__)


def _to_uuid(val):
    """Convert string to UUID if needed."""
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    return UUID(str(val))


async def save_conversation(thread_id: str, user_id: Optional[str] = None,
                            title: Optional[str] = None):
    """Upsert a conversation record. If title is None, only bump updated_at."""
    pool = await get_pool()
    tid = _to_uuid(thread_id)
    uid = _to_uuid(user_id)
    if title is not None:
        await pool.execute("""
            INSERT INTO polycode.chat_conversations (thread_id, user_id, title)
            VALUES ($1, $2, $3)
            ON CONFLICT (thread_id) DO UPDATE
            SET title = $3, updated_at = NOW()
        """, tid, uid, title)
    else:
        await pool.execute("""
            INSERT INTO polycode.chat_conversations (thread_id, user_id, title)
            VALUES ($1, $2, 'New chat')
            ON CONFLICT (thread_id) DO UPDATE
            SET updated_at = NOW()
        """, tid, uid)


async def save_message(thread_id: str, role: str, content: str,
                       message_id: Optional[str] = None, metadata: Optional[dict] = None):
    """Insert a chat message."""
    tid = _to_uuid(thread_id)
    mid = _to_uuid(message_id) if message_id else uuid4()
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO polycode.chat_messages (thread_id, message_id, role, content, metadata)
        VALUES ($1, $2, $3, $4, $5::jsonb)
    """, tid, mid, role, content,
        json.dumps(metadata) if metadata else None)


async def load_conversation_messages(thread_id: str) -> list[dict]:
    """Load all messages for a thread, ordered by creation time."""
    pool = await get_pool()
    tid = _to_uuid(thread_id)
    rows = await pool.fetch("""
        SELECT message_id, role, content, metadata, created_at
        FROM polycode.chat_messages
        WHERE thread_id = $1
        ORDER BY created_at ASC
    """, tid)
    return [
        {
            "message_id": str(r["message_id"]),
            "role": r["role"],
            "content": r["content"],
            "metadata": r["metadata"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def list_conversations(user_id: Optional[str] = None, limit: int = 20) -> list[dict]:
    """List recent conversations, optionally filtered by user."""
    pool = await get_pool()
    if user_id:
        uid = _to_uuid(user_id)
        rows = await pool.fetch("""
            SELECT c.thread_id, c.title, c.updated_at,
                   (SELECT content FROM polycode.chat_messages m
                    WHERE m.thread_id = c.thread_id AND m.role = 'user'
                    ORDER BY m.created_at ASC LIMIT 1) AS first_msg
            FROM polycode.chat_conversations c
            WHERE c.user_id = $1
            ORDER BY c.updated_at DESC
            LIMIT $2
        """, uid, limit)
    else:
        # No user_id — return empty (don't show anonymous chats)
        return []
    return [
        {
            "thread_id": str(r["thread_id"]),
            "title": r["title"],
            "updated_at": r["updated_at"],
            "first_msg": r["first_msg"],
        }
        for r in rows
    ]


async def delete_conversation(thread_id: str):
    """Delete a conversation and its messages (cascade)."""
    pool = await get_pool()
    tid = _to_uuid(thread_id)
    await pool.execute(
        "DELETE FROM polycode.chat_conversations WHERE thread_id = $1", tid
    )
