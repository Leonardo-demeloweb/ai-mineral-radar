"""
bot_scm.py — Sistema de Cadastro Mineiro ANM → OpenSearch

Baixa os 6 CSVs do SCM (dadosabertos.anm.gov.br/SCM/) e executa 3 operações:

  1. mr_tipo_uso_v001    — ~26 tipos de uso únicos (tabela de referência)
  2. mr_substancias_v001 — catálogo de substâncias com embedding semântico
  3. mr_jazidas_v001     — enriquece `uso_substancia` por join no número do processo

Por que 3 índices em vez de colocar tudo no jazidas:
  - mr_substancias_v001 permite resolução semântica de substâncias antes da busca
    ("minerais para baterias EV" → lítio, cobalto, grafita, nióbio)
  - mr_tipo_uso_v001 é tabela de referência para filtros de facets no frontend
  - mr_jazidas_v001 fica com um campo simples `uso_substancia` (keyword)

Uso:
  python -m bots.bot_scm                        # tudo (tipo_uso + substancias + jazidas)
  python -m bots.bot_scm --skip-download        # reusar CSVs já baixados
  python -m bots.bot_scm --only tipo_uso        # só popular mr_tipo_uso_v001
  python -m bots.bot_scm --only substancias     # só popular mr_substancias_v001
  python -m bots.bot_scm --only jazidas         # só enriquecer mr_jazidas_v001
  python -m bots.bot_scm --dry-run              # não escreve no OpenSearch
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterator

import click
import polars as pl
from opensearchpy import OpenSearch, helpers

from bots.bot_anm_direto import get_os_client
from bots.common.embeddings import (
    ZERO_VECTOR,
    embed_batch,
    get_embedding_client as _get_embedding_client,
)
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

SCM_BASE = "https://dadosabertos.anm.gov.br/SCM"
SCM_FILES = [
    "Alvara_de_Pesquisa.csv",
    "Portaria_de_Lavra.csv",
    "Licenciamento.csv",
    "Requerimento_de_Pesquisa.csv",
    "Requerimento_de_Lavra.csv",
    "Registro_de_Extracao_Publicado.csv",
    "Relatorio_de_Pesquisa_Aprovado.csv",
    "Requerimento_de_Licenciamento.csv",
    "Requerimento_de_PLG.csv",
    "PLG.csv",
]

IDX_TIPO_USO    = "mr_tipo_uso_v001"
IDX_SUBSTANCIAS = "mr_substancias_v001"
IDX_JAZIDAS     = "mr_jazidas_v001"

HEADERS = {"User-Agent": settings.anm_user_agent}
EMBED_BATCH_SIZE = 200

# Mapeamento tipo_uso ANM → (grupo, categoria, estrategica)
TIPO_USO_META: dict[str, dict] = {
    # Construção
    "construção civil":          {"grupo": "Minerais de Construção",          "categoria": "nao_metalica",  "estrategica": False},
    "construcao civil":          {"grupo": "Minerais de Construção",          "categoria": "nao_metalica",  "estrategica": False},
    "brita":                     {"grupo": "Minerais de Construção",          "categoria": "nao_metalica",  "estrategica": False},
    # Metálicos
    "metalurgia de ferrosos":    {"grupo": "Minerais Ferrosos",               "categoria": "metalica",      "estrategica": True},
    "metalurgia de nao-ferrosos":{"grupo": "Minerais Não-Ferrosos",           "categoria": "metalica",      "estrategica": True},
    "metalurgia de não-ferrosos":{"grupo": "Minerais Não-Ferrosos",           "categoria": "metalica",      "estrategica": True},
    "metalurgia":                {"grupo": "Minerais Metálicos",              "categoria": "metalica",      "estrategica": True},
    "fabricação de ligas":       {"grupo": "Minerais Metálicos",              "categoria": "metalica",      "estrategica": True},
    # Fertilizantes e agro
    "fertilizantes":             {"grupo": "Minerais para Fertilizantes",     "categoria": "nao_metalica",  "estrategica": True},
    "corretivo de solo":         {"grupo": "Minerais para Fertilizantes",     "categoria": "nao_metalica",  "estrategica": True},
    "insumo agrícola":           {"grupo": "Minerais para Fertilizantes",     "categoria": "nao_metalica",  "estrategica": True},
    # Gemas e pedras
    "gema":                      {"grupo": "Pedras Preciosas e Semipreciosas", "categoria": "gema",          "estrategica": False},
    "gemas":                     {"grupo": "Pedras Preciosas e Semipreciosas", "categoria": "gema",          "estrategica": False},
    "ourivesaria":               {"grupo": "Pedras Preciosas e Semipreciosas", "categoria": "gema",          "estrategica": False},
    "pedra de coleção":          {"grupo": "Pedras Preciosas e Semipreciosas", "categoria": "gema",          "estrategica": False},
    "pedra de talhe":            {"grupo": "Minerais de Construção",          "categoria": "nao_metalica",  "estrategica": False},
    "pedra decorativa":          {"grupo": "Minerais de Construção",          "categoria": "nao_metalica",  "estrategica": False},
    # Energéticos
    "energético":                {"grupo": "Minerais Energéticos",            "categoria": "energetica",    "estrategica": True},
    "energéticos":               {"grupo": "Minerais Energéticos",            "categoria": "energetica",    "estrategica": True},
    "energeticos":               {"grupo": "Minerais Energéticos",            "categoria": "energetica",    "estrategica": True},
    # Radioativos
    "radioativos":               {"grupo": "Minerais Radioativos",            "categoria": "radioativa",    "estrategica": True},
    # Industrial e cerâmica
    "industrial":                {"grupo": "Minerais Industriais",            "categoria": "nao_metalica",  "estrategica": False},
    "cerâmica vermelha":         {"grupo": "Minerais Cerâmicos",              "categoria": "nao_metalica",  "estrategica": False},
    "abrasivo":                  {"grupo": "Minerais Industriais",            "categoria": "nao_metalica",  "estrategica": False},
    "pigmento":                  {"grupo": "Minerais Industriais",            "categoria": "nao_metalica",  "estrategica": False},
    "revestimento":              {"grupo": "Minerais de Construção",          "categoria": "nao_metalica",  "estrategica": False},
    "fabricação de cimento":     {"grupo": "Minerais de Construção",          "categoria": "nao_metalica",  "estrategica": False},
    "fabricação de vidro":       {"grupo": "Minerais Industriais",            "categoria": "nao_metalica",  "estrategica": False},
    "fabricação de cal":         {"grupo": "Minerais de Construção",          "categoria": "nao_metalica",  "estrategica": False},
    # Água mineral
    "água mineral":              {"grupo": "Águas Minerais",                  "categoria": "agua_mineral",  "estrategica": False},
    "agua mineral":              {"grupo": "Águas Minerais",                  "categoria": "agua_mineral",  "estrategica": False},
    "engarrafamento":            {"grupo": "Águas Minerais",                  "categoria": "agua_mineral",  "estrategica": False},
    "balneoterapia":             {"grupo": "Águas Minerais",                  "categoria": "agua_mineral",  "estrategica": False},
    # Outros
    "rejeito de beneficiamento": {"grupo": "Rejeitos",                        "categoria": "rejeito",       "estrategica": False},
    "artesanato  mineral":       {"grupo": "Minerais Artesanais",             "categoria": "gema",          "estrategica": False},
    "artesanato mineral":        {"grupo": "Minerais Artesanais",             "categoria": "gema",          "estrategica": False},
}

def _tipo_uso_meta(tipo_uso: str) -> dict:
    """Retorna metadados do tipo de uso (grupo, categoria, estrategica)."""
    key = tipo_uso.lower().strip()
    return TIPO_USO_META.get(key, {
        "grupo": "Outros Minerais",
        "categoria": "outro",
        "estrategica": False,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_scm_csvs(data_dir: Path, skip: bool) -> list[Path]:
    """Baixa todos os CSVs do SCM. Retorna lista de paths existentes."""
    import httpx
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for fname in SCM_FILES:
        dest = data_dir / fname
        if skip and dest.exists():
            log.info("scm.download.skip", file=fname)
            paths.append(dest)
            continue
        url = f"{SCM_BASE}/{fname}"
        log.info("scm.download.start", url=url)
        try:
            with httpx.stream("GET", url, headers=HEADERS, timeout=120,
                              follow_redirects=True) as resp:
                if resp.status_code == 404:
                    log.warning("scm.download.not_found", file=fname)
                    continue
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(1024 * 256):
                        f.write(chunk)
            log.info("scm.download.done", file=fname,
                     size_mb=round(dest.stat().st_size / 1e6, 1))
            paths.append(dest)
        except Exception as exc:
            log.warning("scm.download.error", file=fname, error=str(exc)[:200])
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# Parse CSVs
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_processo(p: str | None) -> str | None:
    """Remove pontos e espaços do número do processo para normalizar joins."""
    if not p:
        return None
    return re.sub(r'[\s.]', '', str(p).strip())


def _detect_separator(path: Path) -> str:
    """Auto-detecta separador lendo as primeiras 2 linhas do arquivo."""
    try:
        raw = path.read_bytes()[:2000].decode("latin1", errors="replace")
        first_line = raw.split("\n")[0]
        # Conta ocorrências fora de aspas (heurística simples)
        semicolons = first_line.count(";")
        commas = first_line.count(",")
        # Se há muitos `;` OR poucos `,` (vírgulas podem estar em nomes de empresa)
        return ";" if semicolons >= 5 else ","
    except Exception:
        return ","


def _normalize_col(col: str) -> str:
    """Normaliza nome de coluna: remove acentos, parênteses, espaços."""
    return (
        col.strip()
        .replace("ê", "e").replace("â", "a").replace("ã", "a").replace("à", "a")
        .replace("ç", "c").replace("é", "e").replace("á", "a").replace("è", "e")
        .replace("ú", "u").replace("ó", "o").replace("í", "i").replace("ô", "o")
        .replace("(s)", "").replace("(", "").replace(")", "").replace("/", "")
        .strip().lower().replace(" ", "_").replace("-", "_")
    )


def load_scm_dataframe(paths: list[Path]) -> pl.DataFrame:
    """
    Carrega e concatena todos os CSVs do SCM.
    Auto-detecta separador (alguns usam ',' outros ';').
    Usa quote_char=None para tolerar aspas embutidas em nomes de empresa.
    """
    TARGET_COLS = ["processo", "substancia", "tipo_de_uso", "fase_atual",
                   "titular", "situacao"]

    frames = []
    for path in paths:
        sep = _detect_separator(path)
        def _try_polars(path: Path, sep: str) -> pl.DataFrame | None:
            try:
                df = pl.read_csv(
                    path,
                    separator=sep,
                    encoding="latin1",
                    infer_schema_length=0,
                    ignore_errors=True,
                    truncate_ragged_lines=True,
                )
                if len(df.columns) > 3:
                    return df.rename({col: _normalize_col(col) for col in df.columns})
            except Exception:
                pass
            return None

        def _try_python_csv(path: Path, sep: str) -> pl.DataFrame | None:
            """Fallback: usa csv.reader do Python que lida melhor com aspas embutidas."""
            import csv as csvlib
            try:
                rows = []
                with open(path, encoding="latin1", errors="replace") as f:
                    reader = csvlib.reader(f, delimiter=sep, quotechar='"')
                    headers = None
                    for row in reader:
                        if headers is None:
                            headers = [_normalize_col(h) for h in row]
                            continue
                        if len(row) == len(headers):
                            rows.append(row)
                if headers and rows:
                    return pl.DataFrame(
                        {headers[i]: [r[i] for r in rows]
                         for i in range(len(headers))}
                    )
            except Exception:
                pass
            return None

        try:
            df = _try_polars(path, sep)
            # Tenta separador alternativo
            if df is None or len(df.columns) <= 3:
                alt_sep = "," if sep == ";" else ";"
                df2 = _try_polars(path, alt_sep)
                if df2 is not None and len(df2.columns) > (len(df.columns) if df is not None else 0):
                    df = df2
            # Fallback: csv.reader do Python
            if df is None or len(df.columns) <= 3:
                for try_sep in [sep, "," if sep == ";" else ";"]:
                    df2 = _try_python_csv(path, try_sep)
                    if df2 is not None and len(df2.columns) > 3:
                        df = df2
                        break

            # Seleciona apenas colunas de interesse existentes
            available = [c for c in TARGET_COLS if c in df.columns]
            if "processo" not in available or "substancia" not in available:
                log.warning("scm.csv.skip", file=path.name,
                            reason="sem colunas processo/substancia",
                            cols=df.columns[:8])
                continue

            df = df.select(available)
            frames.append(df)
            log.info("scm.csv.loaded", file=path.name, rows=len(df),
                     sep=sep, cols=available)

        except Exception as exc:
            log.warning("scm.csv.error", file=path.name, error=str(exc)[:300])

    if not frames:
        raise RuntimeError("Nenhum CSV do SCM foi carregado com sucesso.")

    combined = pl.concat(frames, how="diagonal")
    log.info("scm.combined", total_rows=len(combined))
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# 1. mr_tipo_uso_v001
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_tipo_uso_fields(client: OpenSearch) -> None:
    """Adiciona campos novos ao mr_tipo_uso_v001 (não recria os já existentes)."""
    try:
        client.indices.put_mapping(
            index=IDX_TIPO_USO,
            body={"properties": {
                "grupo":       {"type": "keyword"},
                "categoria":   {"type": "keyword"},
                "estrategica": {"type": "boolean"},
            }},
        )
    except Exception as exc:
        log.warning("tipo_uso.mapping.skip", reason=str(exc)[:100])


def populate_tipo_uso(client: OpenSearch, df: pl.DataFrame, dry_run: bool) -> dict[str, int]:
    """Popula mr_tipo_uso_v001 com tipos de uso únicos do SCM."""
    col = next((c for c in df.columns if "tipo" in c and "uso" in c), None)
    if not col:
        log.warning("tipo_uso.col_not_found", cols=df.columns)
        return {}

    # Extrai tipos individuais: o campo pode ter múltiplos separados por vírgula
    raw_tipos = (df.select(col).drop_nulls().get_column(col).to_list())
    tipos_set: set[str] = set()
    for raw in raw_tipos:
        for t in str(raw).split(","):
            t = t.strip()
            if t and t.lower() not in ("nan", "none", ""):
                tipos_set.add(t)
    tipos = sorted(tipos_set)

    log.info("tipo_uso.found", total=len(tipos), tipos=tipos[:10])

    tipo_to_id: dict[str, int] = {}
    actions = []
    for i, tipo in enumerate(tipos, start=1):
        meta = _tipo_uso_meta(tipo)
        doc = {
            "id":          i,
            "descricao":   tipo,
            "sigla":       "".join(w[0].upper() for w in tipo.split()[:3]),
            "grupo":       meta["grupo"],
            "categoria":   meta["categoria"],
            "estrategica": meta["estrategica"],
        }
        actions.append({"_index": IDX_TIPO_USO, "_id": str(i), "_source": doc})
        tipo_to_id[tipo.lower().strip()] = i

    if not dry_run:
        _ensure_tipo_uso_fields(client)
        ok, errs = helpers.bulk(client, actions, raise_on_error=False)
        log.info("tipo_uso.indexed", ok=ok, errs=errs)
    else:
        log.info("dry_run.tipo_uso", would_index=len(actions))

    return tipo_to_id


# ─────────────────────────────────────────────────────────────────────────────
# 2. mr_substancias_v001
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_substancias_fields(client: OpenSearch) -> None:
    """Garante que mr_substancias_v001 tem campos tipo_uso."""
    client.indices.put_mapping(
        index=IDX_SUBSTANCIAS,
        body={"properties": {
            "tipo_uso":      {"type": "keyword"},
            "tipo_uso_id":   {"type": "integer"},
        }},
    )


def _build_substancia_embedding_text(nome: str, tipo_uso: str, grupo: str, categoria: str) -> str:
    """
    Texto rico para embedding de substância mineral.
    Quanto mais contexto, melhor a resolução semântica.
    Ex: "substância: Lítio | uso: Metalurgia de não-ferrosos | grupo: Minerais Não-Ferrosos
         | categoria: metálico | aplicações: baterias, eletrônicos, veículos elétricos"
    """
    # Enriquecimento de aplicações por tipo de uso (melhora recall semântico)
    aplicacoes_map = {
        "nao_metalica":  "construção civil, aterro, pavimentação, cerâmica",
        "metalica":      "siderurgia, metalurgia, indústria, exportação, alta tecnologia",
        "energetica":    "energia elétrica, termelétrica, combustível",
        "radioativa":    "energia nuclear, medicina nuclear",
        "gema":          "joalheria, colecionismo, ornamentação",
        "agua_mineral":  "consumo humano, engarrafamento, termalismo",
        "rejeito":       "reaproveitamento industrial",
        "outro":         "uso industrial diverso",
    }
    aplicacoes = aplicacoes_map.get(categoria, "uso mineral")

    parts = [f"substância: {nome}"]
    if tipo_uso:    parts.append(f"uso: {tipo_uso}")
    if grupo:       parts.append(f"grupo: {grupo}")
    if categoria:   parts.append(f"categoria: {categoria.replace('_', ' ')}")
    parts.append(f"aplicações: {aplicacoes}")
    return " | ".join(parts)


def populate_substancias(
    client: OpenSearch,
    df: pl.DataFrame,
    tipo_to_id: dict[str, int],
    embed_client,
    embed_deployment: str,
    dry_run: bool,
    *,
    data_dir: Path | None = None,
    skip_download: bool = False,
) -> None:
    """
    Popula mr_substancias_v001.

    Prioridade:
      1. Tabela oficial ``Substancia.txt`` (microdados-scm.zip, ~862 linhas)
      2. Fallback: nomes distintos nos CSVs SCM (legado dev parcial)
    """
    from bots.bot_sicop import SCM_MICRODADOS_FILE, download_microdados
    from bots.bot_substancias_anm import (
        build_documents,
        index_substancias_catalog,
    )
    from bots.common.anm_substancias import (
        build_scm_tipo_uso_map,
        parse_substancias_microdados,
    )

    base = data_dir or settings.etl_data_dir
    micro_path = base / "scm" / SCM_MICRODADOS_FILE
    if not micro_path.exists() and not dry_run:
        downloaded = download_microdados(base, skip=skip_download)
        if downloaded:
            micro_path = downloaded

    if micro_path.exists():
        official = parse_substancias_microdados(micro_path)
        if len(official) >= 100:
            scm_map = build_scm_tipo_uso_map(df)
            docs = build_documents(official, scm_map, tipo_to_id)
            log.info(
                "substancias.source",
                source="ANM/Substancia.txt",
                total=len(docs),
            )
            index_substancias_catalog(
                client, docs, embed_client, embed_deployment, dry_run,
                replace=True,
            )
            return
        log.warning(
            "substancias.official.empty",
            path=str(micro_path),
            fallback="SCM CSV distinct names",
        )

    sub_col  = next((c for c in df.columns if "substancia" in c), None)
    tipo_col = next((c for c in df.columns if "tipo" in c and "uso" in c), None)

    if not sub_col:
        log.warning("substancias.col_not_found", cols=df.columns)
        return

    # Monta mapa: substância → tipo_uso (pega o mais frequente por substância)
    if tipo_col:
        pairs = (
            df.select([sub_col, tipo_col])
              .drop_nulls()
              .filter(pl.col(sub_col).str.len_chars() > 0)
              .group_by(sub_col)
              .agg(pl.col(tipo_col).mode().first().alias("tipo_uso"))
        )
        subs_df = pairs.rename({sub_col: "nome", "tipo_uso": "tipo_uso"})
    else:
        subs_df = (df.select(sub_col).drop_nulls().unique()
                     .rename({sub_col: "nome"})
                     .with_columns(pl.lit(None).alias("tipo_uso")))

    # Explode substâncias multi-valor (separadas por vírgula)
    rows = subs_df.to_dicts()
    expanded: dict[str, str] = {}
    for r in rows:
        raw_nome = str(r["nome"] or "").strip()
        tipo_raw = str(r.get("tipo_uso") or "").strip()
        # Pega apenas o primeiro tipo de uso individual (mais específico)
        tipo = tipo_raw.split(",")[0].strip() if tipo_raw else ""
        # Algumas linhas têm múltiplas substâncias: "AREIA, CASCALHO"
        for nome in re.split(r'[,;]+', raw_nome):
            nome = nome.strip()
            if nome and nome.lower() not in ("nan", "none", ""):
                key = nome.upper()
                if key not in expanded or (tipo and not expanded.get(key)):
                    expanded[key] = tipo

    log.info("substancias.unique", total=len(expanded))

    # Monta documentos
    docs = []
    for i, (nome_upper, tipo_uso) in enumerate(sorted(expanded.items()), start=1):
        meta = _tipo_uso_meta(tipo_uso) if tipo_uso else {"grupo": "Outros Minerais",
                                                          "categoria": "outro",
                                                          "estrategica": False}
        docs.append({
            "_id":    nome_upper,
            "id":     i,
            "codigo": nome_upper,
            "nome":   nome_upper.title(),
            "nome_normalizado": nome_upper.lower(),
            "tipo_uso":    tipo_uso or None,
            "tipo_uso_id": tipo_to_id.get((tipo_uso or "").lower().strip()),
            "grupo":       meta["grupo"],
            "categoria":   meta["categoria"],
            "estrategica": meta["estrategica"],
            "ativo":       True,
        })

    # Gera embeddings em batches
    log.info("substancias.embedding.start", total=len(docs))
    texts = [
        _build_substancia_embedding_text(
            d["nome"], d.get("tipo_uso") or "", d["grupo"], d["categoria"]
        )
        for d in docs
    ]

    if embed_client:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            chunk = texts[i: i + EMBED_BATCH_SIZE]
            vecs = embed_batch(embed_client, chunk, embed_deployment)
            vectors.extend(vecs)
            log.info("substancias.embed_batch.done", offset=i, chunk=len(chunk))
    else:
        vectors = [ZERO_VECTOR] * len(docs)

    for doc, vec in zip(docs, vectors):
        doc["embedding"] = vec

    if dry_run:
        log.info("dry_run.substancias", would_index=len(docs), sample=docs[:2])
        return

    _ensure_substancias_fields(client)
    actions = [{"_index": IDX_SUBSTANCIAS, "_id": d.pop("_id"), "_source": d}
               for d in docs]
    ok, errs = helpers.bulk(client, actions, raise_on_error=False, chunk_size=100)
    log.info("substancias.indexed", ok=ok, errs=errs)


# ─────────────────────────────────────────────────────────────────────────────
# 1b. mr_tipo_uso_v001 — embeddings semânticos (migração)
# ─────────────────────────────────────────────────────────────────────────────

# Contexto de aplicações por categoria para enriquecer o texto de embedding
_TIPO_USO_APLICACOES: dict[str, str] = {
    "nao_metalica":  (
        "areia, brita, cascalho, calcário, pedra, aterro, pavimentação, pavimento, "
        "fundação, argamassa, concreto, cimento, revestimento, rodovia, obra civil, calçamento"
    ),
    "metalica":      (
        "siderurgia, metalurgia, ferro, aço, alumínio, cobre, ligas metálicas, "
        "exportação, indústria pesada, refinaria, fundição"
    ),
    "energetica":    (
        "carvão, petróleo, xisto, energia elétrica, termelétrica, combustível fóssil, "
        "geração de energia, caldeira"
    ),
    "radioativa":    (
        "urânio, tório, energia nuclear, medicina nuclear, pesquisa atômica, "
        "combustível nuclear, reator"
    ),
    "gema":          (
        "diamante, esmeralda, rubi, safira, topázio, ametista, joalheria, joias, "
        "ourivesaria, colecionismo, ornamentação, pedras preciosas, pedras semipreciosas"
    ),
    "agua_mineral":  (
        "água potável, engarrafamento, balneário, termalismo, spa, consumo humano, "
        "água de fonte, hidroterapia"
    ),
    "rejeito":       (
        "rejeito de beneficiamento, reaproveitamento industrial, co-processamento, "
        "estéril de mina, tailings"
    ),
    "outro":         (
        "uso industrial diverso, pesquisa mineral, aplicações especiais, fertilizante, "
        "corretivo de solo, insumo agrícola, pigmento, abrasivo, cerâmica"
    ),
}


def _build_tipo_uso_embedding_text(descricao: str, grupo: str, categoria: str) -> str:
    """
    Texto rico para embedding de tipo de uso mineral.
    Ex: "tipo de uso mineral: Construção civil | grupo: Minerais de Construção
         | categoria: não metálica | aplicações: areia, brita, pavimentação, aterro"
    """
    aplicacoes = _TIPO_USO_APLICACOES.get(categoria, "uso mineral")
    parts = [f"tipo de uso mineral: {descricao}"]
    if grupo:
        parts.append(f"grupo: {grupo}")
    if categoria:
        parts.append(f"categoria: {categoria.replace('_', ' ')}")
    parts.append(f"aplicações: {aplicacoes}")
    return " | ".join(parts)


def enrich_tipo_uso_embeddings(
    client: OpenSearch,
    embed_client,
    embed_deployment: str,
    dry_run: bool,
) -> None:
    """
    Recria mr_tipo_uso_v001 com campo knn_vector + embeddings semânticos.

    Estratégia (não-destrutiva):
      1. Lê todos os docs existentes do índice atual
      2. Gera embeddings para cada doc via Azure OpenAI
      3. Exclui o índice antigo (sem knn)
      4. Cria o índice novo com knn=True e campo embedding
      5. Reindexa os docs com os vetores gerados
    """
    # ── Lê docs atuais ──
    resp = client.search(
        index=IDX_TIPO_USO,
        body={"size": 100, "_source": True, "query": {"match_all": {}}},
    )
    docs_raw = [h["_source"] for h in resp["hits"]["hits"]]
    if not docs_raw:
        log.warning("tipo_uso_embed.empty", msg="Índice mr_tipo_uso_v001 vazio — execute bot_scm primeiro.")
        return

    log.info("tipo_uso_embed.read", total=len(docs_raw))

    # ── Gera embeddings ──
    texts = [
        _build_tipo_uso_embedding_text(
            d.get("descricao", ""),
            d.get("grupo", ""),
            d.get("categoria", ""),
        )
        for d in docs_raw
    ]

    if embed_client:
        vectors = embed_batch(embed_client, texts, embed_deployment)
        log.info("tipo_uso_embed.vectors_generated", n=len(vectors))
    else:
        vectors = [ZERO_VECTOR] * len(docs_raw)
        log.info("tipo_uso_embed.zero_vectors", reason="embed_client ausente")

    # ── Mapeamento novo com knn=True + campo embedding ──
    mapping_body = {
        "settings": {
            "index": {
                "knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "analysis": {
                "normalizer": {
                    "lower_ascii": {
                        "type": "custom",
                        "filter": ["lowercase", "asciifolding"],
                    }
                },
                "analyzer": {
                    "pt_br": {
                        "type": "brazilian",
                    }
                },
            },
        },
        "mappings": {
            "properties": {
                "id":          {"type": "integer"},
                "descricao":   {
                    "type": "text",
                    "analyzer": "pt_br",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256,
                                           "normalizer": "lower_ascii"}},
                },
                "sigla":       {"type": "keyword"},
                "grupo":       {"type": "keyword"},
                "categoria":   {"type": "keyword"},
                "estrategica": {"type": "boolean"},
                "embedding":   {
                    "type": "knn_vector",
                    "dimension": 1536,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "faiss",
                        "parameters": {"ef_construction": 128, "m": 16},
                    },
                },
            }
        },
    }

    if dry_run:
        log.info("dry_run.tipo_uso_embed",
                 would_reindex=len(docs_raw),
                 sample_text=texts[0] if texts else "")
        return

    # ── Recria o índice ──
    try:
        client.indices.delete(index=IDX_TIPO_USO)
        log.info("tipo_uso_embed.index_deleted")
    except Exception as exc:
        log.warning("tipo_uso_embed.delete_skip", reason=str(exc)[:100])

    client.indices.create(index=IDX_TIPO_USO, body=mapping_body)
    log.info("tipo_uso_embed.index_created", knn=True)

    # ── Reindexa com embeddings ──
    actions = []
    for doc, vec in zip(docs_raw, vectors):
        doc_id = str(doc.get("id", ""))
        doc["embedding"] = vec
        actions.append({"_index": IDX_TIPO_USO, "_id": doc_id, "_source": doc})

    ok, errs = helpers.bulk(client, actions, raise_on_error=False)
    log.info("tipo_uso_embed.indexed", ok=ok, errs=errs)


# ─────────────────────────────────────────────────────────────────────────────
# 3. mr_jazidas_v001 — enriquecimento de uso_substancia
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_jazidas_uso_field(client: OpenSearch) -> None:
    """Adiciona uso_substancia ao mapeamento do mr_jazidas_v001."""
    client.indices.put_mapping(
        index=IDX_JAZIDAS,
        body={"properties": {
            "uso_substancia": {"type": "keyword"},
        }},
    )


def enrich_jazidas_uso(
    client: OpenSearch,
    df: pl.DataFrame,
    dry_run: bool,
) -> None:
    """
    Enriquece mr_jazidas_v001 com uso_substancia via join por numero_processo.

    Se `numero_processo` vier vazio no _source, usa o `_id` do documento
    (mesmo valor na ingestão SIGMINE) para o join normalizado.
    """
    sub_col  = next((c for c in df.columns if "substancia" in c), None)
    tipo_col = next((c for c in df.columns if "tipo" in c and "uso" in c), None)
    proc_col = next((c for c in df.columns if "processo" in c), None)

    if not proc_col or not tipo_col:
        log.warning("jazidas_uso.cols_missing", proc_col=proc_col, tipo_col=tipo_col)
        return

    # Constrói mapa processo → tipo_uso (dedup: pega tipo mais frequente)
    pairs_df = (
        df.select([proc_col, tipo_col])
          .drop_nulls()
          .filter(pl.col(tipo_col).str.len_chars() > 0)
          .group_by(proc_col)
          .agg(pl.col(tipo_col).mode().first().alias("tipo_uso"))
    )

    # Normaliza número do processo (remove pontos)
    processo_map: dict[str, str] = {}
    for row in pairs_df.iter_rows(named=True):
        key = _normalize_processo(row[proc_col])
        if key:
            processo_map[key] = row["tipo_uso"]

    log.info("jazidas_uso.lookup_built", total_processos=len(processo_map))

    if dry_run:
        sample = list(processo_map.items())[:5]
        log.info("dry_run.jazidas_uso", sample=sample)
        return

    _ensure_jazidas_uso_field(client)

    # Scroll mr_jazidas_v001 e atualiza em batch
    scroll_size = 1000
    resp = client.search(
        index=IDX_JAZIDAS,
        scroll="5m",
        size=scroll_size,
        body={"_source": ["numero_processo"], "query": {"match_all": {}}},
    )
    scroll_id = resp["_scroll_id"]
    hits = resp["hits"]["hits"]
    total = resp["hits"]["total"]["value"]
    log.info("jazidas_uso.scroll.start", total=total)

    total_updated = total_errors = matched = processed = 0

    while hits:
        actions = []
        for hit in hits:
            processed += 1
            num = hit["_source"].get("numero_processo") or hit.get("_id")
            proc = _normalize_processo(num)
            uso = processo_map.get(proc)
            if uso:
                matched += 1
                actions.append({
                    "_op_type": "update",
                    "_index":   IDX_JAZIDAS,
                    "_id":      hit["_id"],
                    "doc":      {"uso_substancia": uso},
                })

        if actions:
            ok, errs = helpers.bulk(client, actions, raise_on_error=False, chunk_size=200)
            total_updated += ok
            total_errors  += errs if isinstance(errs, int) else len(errs)

        log.info("jazidas_uso.progress",
                 processed=processed, total=total,
                 matched=matched, updated=total_updated)

        resp = client.scroll(scroll_id=scroll_id, scroll="5m")
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    log.info("jazidas_uso.done",
             total_updated=total_updated, total_errors=total_errors,
             matched=matched, no_match=processed - matched)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--skip-download", is_flag=True, help="Reusar CSVs já baixados")
@click.option("--dry-run",       is_flag=True, help="Não escreve no OpenSearch")
@click.option("--only",
              type=click.Choice(["tipo_uso", "substancias", "jazidas", "tipo_uso_embeddings"]),
              default=None,
              help="Executar apenas uma etapa específica")
@click.option("--no-embeddings", is_flag=True,
              help="Pular geração de embeddings (usa vetores zero)")
def main(
    skip_download: bool,
    dry_run: bool,
    only: str | None,
    no_embeddings: bool,
) -> None:
    """SCM ANM → mr_tipo_uso_v001 + mr_substancias_v001 + enriquece mr_jazidas_v001."""
    t0 = time.time()
    os_client = get_os_client()

    embed_client = None if (no_embeddings or dry_run) else _get_embedding_client()
    embed_deployment = settings.azure_openai_deployment_embedding
    if embed_client:
        log.info("embeddings.enabled", deployment=embed_deployment)
    else:
        log.info("embeddings.disabled")

    # Etapa especial: adicionar embeddings ao mr_tipo_uso_v001 já populado
    if only == "tipo_uso_embeddings":
        log.info("--- etapa especial: embeddings mr_tipo_uso_v001 ---")
        enrich_tipo_uso_embeddings(os_client, embed_client, embed_deployment, dry_run)
        elapsed = round(time.time() - t0, 1)
        log.info("bot_scm.done", elapsed_s=elapsed, dry_run=dry_run)
        return

    data_dir = settings.etl_data_dir / "scm"
    paths = download_scm_csvs(data_dir, skip=skip_download)

    if not paths:
        log.error("scm.no_files", msg="Nenhum CSV SCM encontrado. Verifique a conexão.")
        raise SystemExit(1)

    df = load_scm_dataframe(paths)

    # Etapa 1 — tipo_uso
    tipo_to_id: dict[str, int] = {}
    if only in (None, "tipo_uso"):
        log.info("--- etapa 1: mr_tipo_uso_v001 ---")
        tipo_to_id = populate_tipo_uso(os_client, df, dry_run)

    # Etapa 2 — substancias
    if only in (None, "substancias"):
        log.info("--- etapa 2: mr_substancias_v001 ---")
        populate_substancias(
            os_client, df, tipo_to_id,
            embed_client, embed_deployment, dry_run,
            data_dir=data_dir, skip_download=skip_download,
        )

    # Etapa 3 — enriquece jazidas
    if only in (None, "jazidas"):
        log.info("--- etapa 3: enriquece mr_jazidas_v001 ---")
        enrich_jazidas_uso(os_client, df, dry_run)

    elapsed = round(time.time() - t0, 1)
    log.info("bot_scm.done", elapsed_s=elapsed, dry_run=dry_run)


if __name__ == "__main__":
    main()
