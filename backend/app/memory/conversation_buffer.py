"""
Redis Conversation Buffer
==========================

Short-term memory for AI conversations (S8).

Stores conversation history per session_id in Redis as a bounded list.
Each element is a JSON-serialized LangChain BaseMessage.

Design:
    Key:    conv:{session_id}:messages  (Redis List)
    TTL:    2 hours (resets on each write — keeps active sessions alive)
    Limit:  MAX_MESSAGES most recent messages (auto-trimmed on write)

Supported message types:
    - HumanMessage  (user input)
    - AIMessage     (texto final da assistente — **sem** persistir ``tool_calls``;
      ver nota abaixo sobre API OpenAI)
    - ToolMessage   (resultado de tool — raramente gravado por ``chat.py``)

**Invariante OpenChat / Azure:** um ``AIMessage`` com ``tool_calls`` na fila
precisa ser seguido pelas ``ToolMessage`` correspondentes. O buffer de Redis
não armazena esse bloco completo (persistimos só pares humano/assistente para
o chat), portanto **nunca** serializamos ``tool_calls`` em assistentes.

Usage:
    buffer = RedisConversationBuffer(redis_client)

    history = await buffer.load(session_id)
    await buffer.append(session_id, [human_msg, ai_msg])
    await buffer.clear(session_id)
    count  = await buffer.length(session_id)
"""

import json
import logging
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.core.config import settings
from app.db.redis import RedisClient

logger = logging.getLogger("memory.buffer")


def strip_assistant_tool_calls(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    Remove ``tool_calls`` de mensagens assistente.

    Necessário porque o histórico Redis não guarda as ToolMessage que seguem
    cada pedido de ferramenta — reenviar assistente com ``tool_calls`` quebra
    a API (400: tool_call_ids sem resposta).

    Preserva ``content`` multimodal / blocos tal como estão.
    """
    out: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            out.append(AIMessage(content=m.content))
        else:
            out.append(m)
    return out


# ── Serialization helpers ──────────────────────────────────────────────────────


def _serialize(msg: BaseMessage) -> str:
    """Serialize a LangChain message to a compact JSON string."""
    payload: dict[str, Any] = {
        "type": msg.type,
        "content": msg.content,
    }
    # Não persistir tool_calls em AIMessage — ver docstring do módulo.
    if isinstance(msg, ToolMessage):
        payload["tool_call_id"] = msg.tool_call_id
        if msg.name:
            payload["name"] = msg.name
    return json.dumps(payload, ensure_ascii=False)


def _deserialize(raw: str) -> BaseMessage:
    """Deserialize a JSON string back to a LangChain message."""
    d = json.loads(raw)
    t = d.get("type", "human")
    content = d.get("content", "")

    if t == "human":
        return HumanMessage(content=content)
    elif t == "ai":
        # Ignora tool_calls legados no JSON — eram inválidos para o próximo turno.
        return AIMessage(content=content)
    elif t == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=d.get("tool_call_id", ""),
            name=d.get("name"),
        )
    elif t == "system":
        return SystemMessage(content=content)
    else:
        logger.warning(f"Unknown message type '{t}' — falling back to HumanMessage")
        return HumanMessage(content=content)


# ── Buffer class ───────────────────────────────────────────────────────────────


class RedisConversationBuffer:
    """
    Short-term conversation memory backed by Redis.

    Each session is a Redis List where the most recent MAX_MESSAGES entries
    are kept. Writes reset the TTL so active sessions stay alive and inactive
    ones expire automatically after DEFAULT_TTL seconds.

    Key pattern:  conv:{session_id}:messages
    """

    KEY_PREFIX = "conv"
    DEFAULT_TTL: int = settings.redis_conversation_ttl
    MAX_MESSAGES = 20    # 10 turns (1 HumanMessage + 1 AIMessage each)

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    def _key(self, session_id: str) -> str:
        return f"{self.KEY_PREFIX}:{session_id}:messages"

    async def load(self, session_id: str) -> list[BaseMessage]:
        """
        Load full conversation history for a session.

        Returns an empty list if Redis is unavailable or the session
        doesn't exist yet (first turn of a new conversation).
        """
        key = self._key(session_id)
        raw_list = await self._redis.lrange(key, 0, -1)
        if not raw_list:
            return []

        messages: list[BaseMessage] = []
        for raw in raw_list:
            try:
                messages.append(_deserialize(raw))
            except Exception as e:
                logger.warning(
                    f"Skipping malformed message in session '{session_id}': {e}"
                )

        logger.debug(f"Loaded {len(messages)} messages — session={session_id!r}")
        return messages

    async def append(self, session_id: str, messages: list[BaseMessage]) -> None:
        """
        Append messages to the conversation history.

        After insertion the list is trimmed to MAX_MESSAGES (keeping most recent)
        and the TTL is reset. No-op if Redis is unavailable (fails silently so
        the API keeps working even without Redis).
        """
        if not messages:
            return

        key = self._key(session_id)
        for msg in messages:
            try:
                await self._redis.rpush(key, _serialize(msg))
            except Exception as e:
                logger.warning(
                    f"Failed to persist message for session '{session_id}': {e}"
                )

        # Trim to keep only the most recent MAX_MESSAGES entries
        await self._redis.ltrim(key, -self.MAX_MESSAGES, -1)

        # Reset TTL — active sessions stay alive
        await self._redis.expire(key, self.DEFAULT_TTL)

        logger.debug(f"Appended {len(messages)} messages — session={session_id!r}")

    async def clear(self, session_id: str) -> None:
        """Delete all stored messages for a session."""
        await self._redis.delete(self._key(session_id))
        logger.info(f"Cleared conversation buffer — session={session_id!r}")

    async def length(self, session_id: str) -> int:
        """Return the number of stored messages for a session."""
        if not self._redis.client:
            return 0
        return await self._redis.client.llen(self._key(session_id))
