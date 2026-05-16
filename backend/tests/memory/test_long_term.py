"""
Unit tests for app.memory.long_term (LongTermMemory).

All MongoDB and LLM calls are mocked — no real infrastructure required.
Run with:
    pytest tests/memory/test_long_term.py -v
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.memory.long_term import LongTermMemory, _messages_to_text


# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_db() -> MagicMock:
    """Minimal AsyncIOMotorDatabase mock with two collections."""
    db = MagicMock()

    sessions = MagicMock()
    sessions.find_one = AsyncMock(return_value=None)
    sessions.replace_one = AsyncMock()
    sessions.create_index = AsyncMock()

    def make_cursor(docs):
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=docs)
        return cursor

    sessions.find = MagicMock(return_value=make_cursor([]))

    memory_col = MagicMock()
    memory_col.find_one = AsyncMock(return_value=None)
    memory_col.update_one = AsyncMock()
    memory_col.create_index = AsyncMock()

    db.__getitem__ = MagicMock(side_effect=lambda name: {
        "chat_sessions": sessions,
        "user_memory": memory_col,
    }[name])

    db._sessions_col = sessions
    db._memory_col = memory_col
    return db


def make_messages(n: int = 4) -> list:
    """Alternating human/AI messages."""
    msgs = []
    for i in range(n):
        if i % 2 == 0:
            msgs.append(HumanMessage(content=f"Pergunta {i}"))
        else:
            msgs.append(AIMessage(content=f"Resposta {i}"))
    return msgs


VALID_LLM_RESPONSE = json.dumps({
    "summary": "Usuário buscou fornecedores de areia em MG.",
    "entities": [{"type": "empresa", "id": "12.345.678/0001-99", "nome": "Areial MG Ltda"}],
    "facts": ["Usuário prefere fornecedores em MG", "Foco em areia lavada"],
    "tags": ["areia", "fornecedores", "MG"],
})


# ── _messages_to_text ─────────────────────────────────────────────────────────


class TestMessagesToText:
    def test_human_and_ai_messages(self):
        msgs = [HumanMessage(content="Olá"), AIMessage(content="Oi")]
        result = _messages_to_text(msgs)
        assert "[HUMAN]: Olá" in result
        assert "[AI]: Oi" in result

    def test_system_message_included(self):
        msgs = [SystemMessage(content="Contexto")]
        result = _messages_to_text(msgs)
        assert "[SYSTEM]: Contexto" in result

    def test_empty_messages(self):
        assert _messages_to_text([]) == ""

    def test_blank_content_skipped(self):
        msgs = [HumanMessage(content="   "), AIMessage(content="Resposta")]
        result = _messages_to_text(msgs)
        assert "[HUMAN]" not in result
        assert "[AI]: Resposta" in result

    def test_long_content_truncated_at_800(self):
        long_content = "x" * 1000
        msgs = [HumanMessage(content=long_content)]
        result = _messages_to_text(msgs)
        assert len(result.split(": ", 1)[1]) == 800


# ── summarize_and_save ────────────────────────────────────────────────────────


@pytest.mark.anyio
class TestSummarizeAndSave:
    async def test_skip_if_too_few_messages(self):
        db = make_db()
        ltm = LongTermMemory(db)
        result = await ltm.summarize_and_save(
            session_id="s1", user_id="u1", messages=make_messages(3)
        )
        assert result is None
        db._sessions_col.replace_one.assert_not_called()

    async def test_skip_if_already_summarized(self):
        db = make_db()
        existing = {"session_id": "s1", "summary": "já existe"}
        db._sessions_col.find_one = AsyncMock(return_value=existing)

        ltm = LongTermMemory(db)
        result = await ltm.summarize_and_save(
            session_id="s1", user_id="u1", messages=make_messages(6)
        )
        assert result == existing
        db._sessions_col.replace_one.assert_not_called()

    async def test_happy_path_saves_document(self):
        db = make_db()
        llm_response = MagicMock()
        llm_response.content = VALID_LLM_RESPONSE

        with patch("app.memory.long_term._build_summarizer") as mock_build:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=llm_response)
            mock_build.return_value = mock_llm

            ltm = LongTermMemory(db)
            result = await ltm.summarize_and_save(
                session_id="s1",
                user_id="u1",
                messages=make_messages(6),
                obra_id="obra-abc",
            )

        assert result is not None
        assert result["session_id"] == "s1"
        assert result["user_id"] == "u1"
        assert result["obra_id"] == "obra-abc"
        assert "Usuário buscou fornecedores" in result["summary"]
        assert result["tags"] == ["areia", "fornecedores", "MG"]
        assert result["turn_count"] == 3  # 6 msgs, 3 human
        db._sessions_col.replace_one.assert_called_once()

    async def test_llm_failure_uses_fallback(self):
        db = make_db()

        with patch("app.memory.long_term._build_summarizer") as mock_build:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))
            mock_build.return_value = mock_llm

            ltm = LongTermMemory(db)
            result = await ltm.summarize_and_save(
                session_id="s2", user_id="u1", messages=make_messages(4)
            )

        assert result is not None
        assert result["summary"] != ""
        assert result["entities"] == []
        # fallback doc does not include "facts" key (merged separately)
        assert "facts" not in result
        db._sessions_col.replace_one.assert_called_once()

    async def test_llm_invalid_json_uses_fallback(self):
        db = make_db()
        bad_response = MagicMock()
        bad_response.content = "não é json"

        with patch("app.memory.long_term._build_summarizer") as mock_build:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=bad_response)
            mock_build.return_value = mock_llm

            ltm = LongTermMemory(db)
            result = await ltm.summarize_and_save(
                session_id="s3", user_id="u1", messages=make_messages(4)
            )

        assert result is not None
        assert result["tags"] == []

    async def test_llm_strips_markdown_fences(self):
        db = make_db()
        wrapped = f"```json\n{VALID_LLM_RESPONSE}\n```"
        llm_response = MagicMock()
        llm_response.content = wrapped

        with patch("app.memory.long_term._build_summarizer") as mock_build:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=llm_response)
            mock_build.return_value = mock_llm

            ltm = LongTermMemory(db)
            result = await ltm.summarize_and_save(
                session_id="s4", user_id="u1", messages=make_messages(4)
            )

        assert "Usuário buscou" in result["summary"]

    async def test_facts_trigger_user_memory_merge(self):
        db = make_db()
        llm_response = MagicMock()
        llm_response.content = VALID_LLM_RESPONSE

        with patch("app.memory.long_term._build_summarizer") as mock_build:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=llm_response)
            mock_build.return_value = mock_llm

            ltm = LongTermMemory(db)
            await ltm.summarize_and_save(
                session_id="s5", user_id="u1", messages=make_messages(4)
            )

        # _merge_user_facts calls update_one at least once for facts
        assert db._memory_col.update_one.call_count >= 1

    async def test_no_facts_skips_memory_merge(self):
        db = make_db()
        no_facts_response = json.dumps({
            "summary": "Conversa curta.",
            "entities": [],
            "facts": [],
            "tags": ["geral"],
        })
        llm_response = MagicMock()
        llm_response.content = no_facts_response

        with patch("app.memory.long_term._build_summarizer") as mock_build:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=llm_response)
            mock_build.return_value = mock_llm

            ltm = LongTermMemory(db)
            await ltm.summarize_and_save(
                session_id="s6", user_id="u1", messages=make_messages(4)
            )

        db._memory_col.update_one.assert_not_called()


# ── load_user_memory ──────────────────────────────────────────────────────────


@pytest.mark.anyio
class TestLoadUserMemory:
    async def test_returns_none_when_not_found(self):
        db = make_db()
        ltm = LongTermMemory(db)
        result = await ltm.load_user_memory("unknown-user")
        assert result is None

    async def test_returns_document_when_found(self):
        db = make_db()
        doc = {"user_id": "u1", "facts": ["fact1"]}
        db._memory_col.find_one = AsyncMock(return_value=doc)

        ltm = LongTermMemory(db)
        result = await ltm.load_user_memory("u1")
        assert result["facts"] == ["fact1"]


# ── get_recent_sessions ───────────────────────────────────────────────────────


@pytest.mark.anyio
class TestGetRecentSessions:
    async def test_returns_empty_list_when_none(self):
        db = make_db()
        ltm = LongTermMemory(db)
        result = await ltm.get_recent_sessions("u1")
        assert result == []

    async def test_returns_sessions_sorted(self):
        db = make_db()
        sessions_data = [
            {"session_id": "s2", "summary": "Segunda", "ended_at": datetime(2024, 2, 1, tzinfo=timezone.utc)},
            {"session_id": "s1", "summary": "Primeira", "ended_at": datetime(2024, 1, 1, tzinfo=timezone.utc)},
        ]

        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=sessions_data)
        db._sessions_col.find = MagicMock(return_value=cursor)

        ltm = LongTermMemory(db)
        result = await ltm.get_recent_sessions("u1", limit=5)
        assert len(result) == 2
        assert result[0]["session_id"] == "s2"


# ── build_context_block ───────────────────────────────────────────────────────


@pytest.mark.anyio
class TestBuildContextBlock:
    async def test_returns_none_when_no_data(self):
        db = make_db()
        ltm = LongTermMemory(db)
        result = await ltm.build_context_block("unknown-user")
        assert result is None

    async def test_includes_facts(self):
        db = make_db()
        db._memory_col.find_one = AsyncMock(return_value={
            "user_id": "u1",
            "facts": ["Prefere MG", "Usa areia lavada"],
            "fornecedores_avaliados": [],
        })

        ltm = LongTermMemory(db)
        result = await ltm.build_context_block("u1")
        assert result is not None
        assert "Prefere MG" in result
        assert "Usa areia lavada" in result

    async def test_includes_fornecedores(self):
        db = make_db()
        db._memory_col.find_one = AsyncMock(return_value={
            "user_id": "u1",
            "facts": [],
            "fornecedores_avaliados": [
                {"cnpj": "12.345.678/0001-99", "nome": "Areial MG", "data": "2024-01-01"}
            ],
        })

        ltm = LongTermMemory(db)
        result = await ltm.build_context_block("u1")
        assert "Areial MG" in result
        assert "12.345.678/0001-99" in result

    async def test_includes_recent_sessions(self):
        db = make_db()
        now = datetime(2024, 3, 1, 10, 30, tzinfo=timezone.utc)
        sessions_data = [{"session_id": "s1", "summary": "Buscou areia", "ended_at": now, "tags": []}]

        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=sessions_data)
        db._sessions_col.find = MagicMock(return_value=cursor)

        ltm = LongTermMemory(db)
        result = await ltm.build_context_block("u1")
        assert "Buscou areia" in result

    async def test_returns_none_when_memory_and_sessions_empty(self):
        db = make_db()
        db._memory_col.find_one = AsyncMock(return_value={
            "user_id": "u1",
            "facts": [],
            "fornecedores_avaliados": [],
        })

        ltm = LongTermMemory(db)
        result = await ltm.build_context_block("u1")
        assert result is None
