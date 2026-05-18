"""
Checkpoint em JSON para bots WFS paginados (SIGEF, SICAR, etc.).

Persiste progresso por UF (startIndex WFS + contadores) em
``{etl_data_dir}/checkpoints/{bot_name}.json`` para retomar após interrupção.

Os documentos usam ``_id`` estável (parcela_codigo / cod_car); reindexar a
mesma página é idempotente.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bots.common.settings import settings

CHECKPOINT_VERSION = 1
CHECKPOINT_DIR = settings.etl_data_dir / "checkpoints"


def checkpoint_path(bot_name: str) -> Path:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    return CHECKPOINT_DIR / f"{bot_name}.json"


def load_checkpoint(bot_name: str) -> dict[str, Any]:
    path = checkpoint_path(bot_name)
    if not path.exists():
        return _empty_state(bot_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_state(bot_name)
    if data.get("version") != CHECKPOINT_VERSION:
        return _empty_state(bot_name)
    if data.get("bot") != bot_name:
        return _empty_state(bot_name)
    data.setdefault("ufs", {})
    return data


def save_checkpoint(state: dict[str, Any], *, persist: bool = True) -> None:
    if not persist:
        return
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = checkpoint_path(state["bot"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def reset_checkpoint(bot_name: str) -> None:
    path = checkpoint_path(bot_name)
    if path.exists():
        path.unlink()


def _empty_state(bot_name: str) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "bot": bot_name,
        "updated_at": None,
        "ufs": {},
    }


def get_uf_entry(state: dict[str, Any], uf: str, phase: str) -> dict[str, Any]:
    key = f"{uf.upper()}:{phase}"
    ufs = state.setdefault("ufs", {})
    if key not in ufs:
        ufs[key] = {
            "uf": uf.upper(),
            "phase": phase,
            "status": "pending",
            "wfs_start_index": 0,
            "docs_parsed": 0,
            "docs_indexed": 0,
            "last_error": None,
        }
    return ufs[key]


def mark_uf_done(state: dict[str, Any], uf: str, phase: str, docs_indexed: int) -> None:
    entry = get_uf_entry(state, uf, phase)
    entry["status"] = "done"
    entry["wfs_start_index"] = 0
    entry["docs_indexed"] = docs_indexed
    entry["last_error"] = None
    save_checkpoint(state)


def mark_uf_failed(
    state: dict[str, Any],
    uf: str,
    phase: str,
    *,
    wfs_start_index: int,
    docs_parsed: int,
    docs_indexed: int,
    error: str,
    persist: bool = True,
) -> None:
    entry = get_uf_entry(state, uf, phase)
    entry["status"] = "failed"
    entry["wfs_start_index"] = wfs_start_index
    entry["docs_parsed"] = docs_parsed
    entry["docs_indexed"] = docs_indexed
    entry["last_error"] = error[:500]
    save_checkpoint(state, persist=persist)


def update_uf_progress(
    state: dict[str, Any],
    uf: str,
    phase: str,
    *,
    wfs_start_index: int,
    docs_parsed: int,
    docs_indexed: int = 0,
    persist: bool = True,
) -> None:
    entry = get_uf_entry(state, uf, phase)
    entry["status"] = "in_progress"
    entry["wfs_start_index"] = wfs_start_index
    entry["docs_parsed"] = docs_parsed
    if docs_indexed:
        entry["docs_indexed"] = docs_indexed
    entry["last_error"] = None
    save_checkpoint(state, persist=persist)


def should_skip_uf(state: dict[str, Any], uf: str, phase: str, *, resume: bool) -> bool:
    if not resume:
        return False
    key = f"{uf.upper()}:{phase}"
    entry = state.get("ufs", {}).get(key)
    return entry is not None and entry.get("status") == "done"


def resume_start_index(state: dict[str, Any], uf: str, phase: str, *, resume: bool) -> int:
    if not resume:
        return 0
    key = f"{uf.upper()}:{phase}"
    entry = state.get("ufs", {}).get(key)
    if not entry:
        return 0
    if entry.get("status") == "done":
        return 0
    return int(entry.get("wfs_start_index") or 0)
