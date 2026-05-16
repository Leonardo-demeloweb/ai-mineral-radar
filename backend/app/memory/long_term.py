"""
Long-Term Memory
================

Cross-session memory persisted in MongoDB.

Two collections:
    - chat_sessions: summarized conversation history per session
    - user_memory:   accumulated user profile (facts, preferences)

The summarization is performed asynchronously via LLM (gpt-4o-mini)
so it never blocks the chat response path.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_openai import AzureChatOpenAI
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.memory.prompts import SUMMARIZE_SESSION_PROMPT
from mcp_servers.common.config import mcp_settings

logger = logging.getLogger("memory.long_term")

SESSIONS_COLLECTION = "chat_sessions"
MEMORY_COLLECTION = "user_memory"


def _build_summarizer() -> AzureChatOpenAI:
    """Build a cheap LLM for summarization tasks."""
    return AzureChatOpenAI(
        azure_deployment=mcp_settings.azure_openai_chat_deployment,
        azure_endpoint=mcp_settings.azure_openai_endpoint,
        api_key=mcp_settings.azure_openai_api_key,
        api_version=mcp_settings.azure_openai_api_version,
        temperature=0.0,
        max_tokens=1024,
    )


def _messages_to_text(messages: list[BaseMessage]) -> str:
    """Convert LangChain messages to a readable conversation string."""
    lines: list[str] = []
    for msg in messages:
        role = msg.type.upper()
        content = str(msg.content)[:800]
        if content.strip():
            lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


def _extract_tool_usage(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """
    Extract tool usage statistics from a message list.

    Counts each unique tool name that appears in ToolMessage.name or in
    AIMessage.tool_calls, returning a sorted list of {name, count} dicts.
    This leverages the serialization already stored in Redis so no extra
    data is needed — just iterate over the in-memory message list.
    """
    counter: Counter[str] = Counter()

    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name:
            counter[msg.name] += 1
        elif isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name") or tc.get("function", {}).get("name", "unknown")
                counter[name] += 1

    return [{"name": name, "count": count} for name, count in counter.most_common()]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create MongoDB indexes for memory collections (idempotent)."""
    sessions = db[SESSIONS_COLLECTION]
    await sessions.create_index("session_id", unique=True)
    await sessions.create_index([("user_id", 1), ("ended_at", -1)])
    await sessions.create_index("projeto_id")
    await sessions.create_index("tags")

    memory = db[MEMORY_COLLECTION]
    await memory.create_index("user_id", unique=True)

    logger.info("Long-term memory indexes ensured")


class LongTermMemory:
    """
    Cross-session memory backed by MongoDB.

    Provides:
    - Session summarization via LLM
    - User profile accumulation (facts, preferences)
    - Context loading for new sessions
    """

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db
        self._sessions = db[SESSIONS_COLLECTION]
        self._memory = db[MEMORY_COLLECTION]

    # ── Session summarization ──────────────────────────────────────────

    async def summarize_and_save(
        self,
        session_id: str,
        user_id: str,
        messages: list[BaseMessage],
        projeto_id: str | None = None,
        route_history: list[str] | None = None,
        force_update: bool = False,
    ) -> dict[str, Any] | None:
        """
        Summarize a conversation via LLM and persist to chat_sessions.

        Returns the summary document or None if summarization fails.
        Skips if session already summarized or has <4 messages.

        Args:
            force_update: When True, overwrites any existing summary. Use before
                          Redis LTRIM to capture the full conversation before
                          old messages are silently dropped.
        """
        if len(messages) < 4:
            logger.debug(f"Session {session_id} too short ({len(messages)} msgs) — skipping")
            return None

        existing = await self._sessions.find_one({"session_id": session_id})
        if existing and not force_update:
            logger.debug(f"Session {session_id} already summarized — skipping")
            return existing

        conversation_text = _messages_to_text(messages)

        try:
            llm = _build_summarizer()
            prompt = SUMMARIZE_SESSION_PROMPT.format(conversation=conversation_text)
            response = await llm.ainvoke(prompt)
            raw = str(response.content).strip()

            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            parsed = json.loads(raw)
        except Exception as e:
            logger.warning(f"LLM summarization failed for session {session_id}: {e}")
            parsed = {
                "summary": conversation_text[:500],
                "entities": [],
                "facts": [],
                "tags": [],
            }

        now = datetime.now(timezone.utc)
        doc = {
            "session_id": session_id,
            "user_id": user_id,
            "projeto_id": projeto_id,
            "started_at": now,
            "ended_at": now,
            "turn_count": len([m for m in messages if m.type == "human"]),
            "route_history": route_history or [],
            "summary": parsed.get("summary", ""),
            "entities": parsed.get("entities", []),
            "tags": parsed.get("tags", []),
            "tool_usage": _extract_tool_usage(messages),
        }

        await self._sessions.replace_one(
            {"session_id": session_id}, doc, upsert=True
        )

        logger.info(f"Session {session_id} summarized and saved to MongoDB")

        new_facts = parsed.get("facts", [])
        if new_facts:
            await self._merge_user_facts(user_id, new_facts, parsed.get("entities", []))

        return doc

    # ── User memory ────────────────────────────────────────────────────

    async def _merge_user_facts(
        self,
        user_id: str,
        new_facts: list[str],
        entities: list[dict],
    ) -> None:
        """Merge new facts into the user's persistent memory."""
        now = datetime.now(timezone.utc)

        await self._memory.update_one(
            {"user_id": user_id},
            {
                "$set": {"updated_at": now},
                "$addToSet": {
                    "facts": {"$each": new_facts[:5]},
                },
                "$setOnInsert": {
                    "user_id": user_id,
                    "created_at": now,
                    "preferences": {},
                    "projetos_ativos": [],
                    "fornecedores_avaliados": [],
                },
            },
            upsert=True,
        )

        for entity in entities:
            if entity.get("type") == "empresa" and entity.get("id"):
                await self._memory.update_one(
                    {"user_id": user_id},
                    {
                        "$addToSet": {
                            "fornecedores_avaliados": {
                                "cnpj": entity["id"],
                                "nome": entity.get("nome", ""),
                                "data": now.isoformat(),
                            }
                        }
                    },
                )

        logger.debug(f"Merged {len(new_facts)} facts for user {user_id}")

    async def load_user_memory(self, user_id: str) -> dict[str, Any] | None:
        """Load the user's persistent memory document."""
        return await self._memory.find_one(
            {"user_id": user_id}, {"_id": 0}
        )

    async def get_recent_sessions(
        self, user_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Return the most recent summarized sessions for a user."""
        cursor = self._sessions.find(
            {"user_id": user_id},
            {"_id": 0, "session_id": 1, "summary": 1, "tags": 1, "ended_at": 1, "projeto_id": 1},
        ).sort("ended_at", -1).limit(limit)

        return await cursor.to_list(length=limit)

    async def get_tool_usage_stats(
        self,
        user_id: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Aggregate tool usage across sessions using MongoDB pipeline.

        Filters by user_id and/or session_id (both optional).
        Returns a list of {name, total_calls, session_count} sorted by total_calls desc.
        """
        match: dict[str, Any] = {"tool_usage": {"$exists": True, "$ne": []}}
        if user_id:
            match["user_id"] = user_id
        if session_id:
            match["session_id"] = session_id

        pipeline = [
            {"$match": match},
            {"$unwind": "$tool_usage"},
            {
                "$group": {
                    "_id": "$tool_usage.name",
                    "total_calls": {"$sum": "$tool_usage.count"},
                    "session_count": {"$sum": 1},
                }
            },
            {"$sort": {"total_calls": -1}},
            {"$limit": limit},
            {
                "$project": {
                    "_id": 0,
                    "name": "$_id",
                    "total_calls": 1,
                    "session_count": 1,
                }
            },
        ]

        return await self._sessions.aggregate(pipeline).to_list(length=limit)

    # ── Context injection ──────────────────────────────────────────────

    async def build_context_block(self, user_id: str) -> str | None:
        """
        Build a text block with user context for injection into the system prompt.

        Returns None if no long-term memory exists for this user.
        """
        memory = await self.load_user_memory(user_id)
        sessions = await self.get_recent_sessions(user_id, limit=3)

        if not memory and not sessions:
            return None

        parts: list[str] = []

        if memory:
            facts = memory.get("facts", [])
            if facts:
                parts.append("Fatos conhecidos sobre o usuário:")
                for f in facts[:8]:
                    parts.append(f"  - {f}")

            fornecedores = memory.get("fornecedores_avaliados", [])
            if fornecedores:
                parts.append("Fornecedores já consultados:")
                for forn in fornecedores[:5]:
                    parts.append(f"  - {forn.get('nome', 'N/A')} (CNPJ: {forn.get('cnpj', 'N/A')})")

        if sessions:
            parts.append("Sessões recentes:")
            for s in sessions:
                date = ""
                if s.get("ended_at"):
                    date = s["ended_at"].strftime("%d/%m %H:%M") if hasattr(s["ended_at"], "strftime") else str(s["ended_at"])[:16]
                parts.append(f"  - [{date}] {s.get('summary', 'Sem resumo')[:200]}")

        return "\n".join(parts) if parts else None
