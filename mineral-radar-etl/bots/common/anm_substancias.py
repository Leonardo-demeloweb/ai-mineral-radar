"""
Catálogo oficial de substâncias ANM (Cadastro Mineiro / SCM microdados).

Fonte: ``microdados-scm.zip`` → ``microdados-scm/Substancia.txt``
(~862 registros, ``IDSubstancia`` + ``NMSubstancia``).

Usado por ``bot_substancias_anm.py`` e ``bot_scm.py`` (etapa substâncias).
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from io import StringIO
from pathlib import Path
from typing import Any

import polars as pl

from bots.common.logging import get_logger

log = get_logger(__name__)

# Caminhos conhecidos dentro do ZIP SCM (relacional ANM)
SUBSTANCIA_ZIP_CANDIDATES = (
    "microdados-scm/Substancia.txt",
    "microdados-scm/Substancias.txt",
    "microdados-scm/substancia.txt",
)

# Palavras-chave → categoria estratégica (SPEC MineralRadar)
_CATEGORIA_ESTRATEGICA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "terra_rara": (
        "terr", "raro", "lant", "cerio", "neodimio", "praseodimio", "itrio",
        "escandio", "europio", "gadolinio", "terbio", "disprosio", "holmio",
        "erbio", "tulio", "iterbio", "lutecio", "niobio", "tantalo",
    ),
    "litio": ("litio", "lithium", "spodumeno", "lepidolito"),
    "niobio": ("niobio", "columbio", "pirocloro"),
    "cobalto": ("cobalto",),
    "grafita": ("grafita", "grafite"),
    "uranio": ("uranio", "urânio", "torio", "tório"),
    "manganes": ("manganes", "manganês"),
    "tungstenio": ("tungstenio", "wolframio", "scheelita"),
    "fosfato": ("fosfato", "apatita"),
}


def normalize_nome(nome: str) -> str:
    """Lowercase + sem acentos — alinha com ``substancias_desc.keyword`` em jazidas."""
    if not nome:
        return ""
    s = unicodedata.normalize("NFKD", nome.strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).lower().strip()


def _normalize_col(col: str) -> str:
    return (
        col.strip()
        .replace("ê", "e").replace("â", "a").replace("ã", "a").replace("à", "a")
        .replace("ç", "c").replace("é", "e").replace("á", "a").replace("è", "e")
        .replace("ú", "u").replace("ó", "o").replace("í", "i").replace("ô", "o")
        .strip()
        .lower()
        .replace(" ", "_")
    )


def _infer_categoria_estrategica(nome: str) -> str | None:
    key = normalize_nome(nome)
    for cat, kws in _CATEGORIA_ESTRATEGICA_KEYWORDS.items():
        if any(kw in key for kw in kws):
            return cat
    return None


def _pick_col(cols: dict[str, str], *candidates: str) -> str | None:
    for c in candidates:
        if c in cols:
            return cols[c]
    return None


def parse_substancias_microdados(zip_path: Path) -> list[dict[str, Any]]:
    """
    Lê a tabela oficial ``Substancia`` do ZIP de microdados SCM.

    Returns:
        Lista de dicts com ``id_anm``, ``nome``, ``nome_normalizado``, ``ativo``.
    """
    if not zip_path.exists():
        log.warning("substancias.zip.missing", path=str(zip_path))
        return []

    inner_name: str | None = None
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for candidate in SUBSTANCIA_ZIP_CANDIDATES:
            if candidate in names:
                inner_name = candidate
                break
        if not inner_name:
            # fallback: qualquer arquivo que pareça tabela de substância
            for n in sorted(names):
                base = n.rsplit("/", 1)[-1].lower()
                if base in ("substancia.txt", "substancias.txt"):
                    inner_name = n
                    break
        if not inner_name:
            log.warning(
                "substancias.zip.no_table",
                path=str(zip_path),
                hint="esperado microdados-scm/Substancia.txt",
                sample=sorted(names)[:15],
            )
            return []

        with zf.open(inner_name) as f:
            content = f.read().decode("latin1", errors="replace")

    try:
        df = pl.read_csv(
            StringIO(content),
            separator=";",
            infer_schema_length=0,
            ignore_errors=True,
            truncate_ragged_lines=True,
        )
    except Exception as exc:
        log.error("substancias.parse_failed", file=inner_name, error=str(exc)[:200])
        return []

    col_map = {_normalize_col(c): c for c in df.columns}
    id_col = _pick_col(col_map, "idsubstancia", "id_substancia", "codigo")
    nome_col = _pick_col(
        col_map,
        "nmsubstancia",
        "nm_substancia",
        "nome_substancia",
        "nome",
        "descricao",
    )
    ativo_col = _pick_col(col_map, "btativo", "ativo", "situacao")

    if not nome_col:
        log.error(
            "substancias.cols_missing",
            file=inner_name,
            columns=list(df.columns)[:20],
        )
        return []

    rows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for row in df.iter_rows(named=True):
        nome_raw = str(row.get(nome_col) or "").strip()
        if not nome_raw or nome_raw.lower() in ("nan", "none", ""):
            continue

        id_anm: int | None = None
        if id_col:
            raw_id = str(row.get(id_col) or "").strip()
            if raw_id.isdigit():
                id_anm = int(raw_id)

        if id_anm is not None and id_anm in seen_ids:
            continue
        if id_anm is not None:
            seen_ids.add(id_anm)

        ativo = True
        if ativo_col:
            v = str(row.get(ativo_col) or "").strip().upper()
            if v in ("N", "NAO", "NÃO", "0", "FALSE", "INATIVO"):
                ativo = False

        nome_norm = normalize_nome(nome_raw)
        cat_est = _infer_categoria_estrategica(nome_raw)

        rows.append({
            "id_anm":       id_anm,
            "nome":         nome_raw,
            "nome_normalizado": nome_norm,
            "ativo":        ativo,
            "categoria_estrategica": cat_est,
        })

    log.info(
        "substancias.official.parsed",
        file=inner_name,
        total=len(rows),
        com_id_anm=sum(1 for r in rows if r.get("id_anm") is not None),
    )
    return rows


def build_scm_tipo_uso_map(df: pl.DataFrame) -> dict[str, str]:
    """
    Mapa nome_upper → tipo_uso mais frequente nos CSVs SCM (para enriquecer o catálogo).
    """
    sub_col = next((c for c in df.columns if "substancia" in c), None)
    tipo_col = next((c for c in df.columns if "tipo" in c and "uso" in c), None)
    if not sub_col:
        return {}

    if tipo_col:
        pairs = (
            df.select([sub_col, tipo_col])
            .drop_nulls()
            .filter(pl.col(sub_col).str.len_chars() > 0)
            .group_by(sub_col)
            .agg(pl.col(tipo_col).mode().first().alias("tipo_uso"))
        )
        subs_df = pairs
    else:
        return {}

    expanded: dict[str, str] = {}
    for r in subs_df.to_dicts():
        raw_nome = str(r.get(sub_col) or "").strip()
        tipo_raw = str(r.get("tipo_uso") or "").strip()
        tipo = tipo_raw.split(",")[0].strip() if tipo_raw else ""
        for nome in re.split(r"[,;]+", raw_nome):
            nome = nome.strip()
            if nome and nome.lower() not in ("nan", "none", ""):
                key = nome.upper()
                if key not in expanded or (tipo and not expanded.get(key)):
                    expanded[key] = tipo
    return expanded
