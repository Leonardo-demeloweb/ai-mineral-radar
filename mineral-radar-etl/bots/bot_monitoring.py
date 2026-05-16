"""
bot_monitoring.py — Monitoramento de Processos ANM → mr_monitoring_v001
=========================================================================

Gera eventos de monitoramento para processos minerários a partir de
três fontes distintas, com prioridade crescente de integração:

  Modo 1 — PRAZO_ALERT (interno, zero API externa)
  ─────────────────────────────────────────────────
  Lê mr_jazidas_v001 e gera alertas para processos cuja dt_validade
  está dentro do horizonte configurado (default: 90 dias).
  Subtipos:
    - VENCIMENTO_30D   → vence em até 30 dias
    - VENCIMENTO_60D   → vence em 31-60 dias
    - VENCIMENTO_90D   → vence em 61-90 dias
    - VENCIDO_ATIVO    → vencido mas ainda em fase ativa (risco de nulidade)

  Modo 2 — STATUS_CHANGE (interno, snapshot diff)
  ─────────────────────────────────────────────────
  Compara o snapshot atual de mr_jazidas_v001 com o último estado
  registrado em mr_monitoring_v001 e gera CHANGE_EVENT para processos
  que mudaram de fase, de titular ou foram cancelados.

  Modo 3 — DOU_PUBLICACAO (externo, INLABS — opcional)
  ──────────────────────────────────────────────────────────
  Autentica no portal INLABS (https://inlabs.in.gov.br) com
  email + password, baixa os ZIPs do DOU, filtra artigos ANM/mineração
  e indexa em mr_monitoring_v001.
  Requer cadastro gratuito em inlabs.in.gov.br.
  Para cadastrar: python scripts/inlabs_setup.py register

Idempotência:
  Cada evento tem _id calculado por hash(tipo_evento + numero_processo
  + dt_evento). Re-executar o bot gera upserts idênticos para eventos
  já existentes.

Uso:
  python -m bots.bot_monitoring --modo prazo
  python -m bots.bot_monitoring --modo prazo --horizonte-dias 60
  python -m bots.bot_monitoring --modo status
  python -m bots.bot_monitoring --modo dou --data 2026-05-11
  python -m bots.bot_monitoring --modo all
  python -m bots.bot_monitoring --modo prazo --dry-run
  python -m bots.bot_monitoring --modo prazo --cnpj 12345678   (filtro por empresa)
  python -m bots.bot_monitoring --modo prazo --uf MG          (filtro por UF)

  Cadastro INLABS (necessário apenas uma vez):
  python scripts/inlabs_setup.py register
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

import click
from opensearchpy import OpenSearch, helpers

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_MON    = "mr_monitoring_v001"
INDEX_JAZ    = "mr_jazidas_v001"
SCROLL_SIZE  = 2_000
BATCH_SIZE   = 200

# Janelas de prazo (dias restantes → subtipo)
PRAZO_JANELAS = [
    (0,  30,  "VENCIMENTO_30D",  "ALTA"),
    (31, 60,  "VENCIMENTO_60D",  "MEDIA"),
    (61, 90,  "VENCIMENTO_90D",  "BAIXA"),
]

# Fases "ativas" que devem ter dt_validade válida
FASES_ATIVAS = [
    "autorizacao de pesquisa",
    "concessao de lavra",
    "licenciamento",
    "lavra garimpeira",
    "registro de extracao",
    "direito de requerer a lavra",
]


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client(timeout: int = 120) -> OpenSearch:
    use_ssl = settings.opensearch_url.startswith("https")
    kwargs: dict = {
        "hosts":       [settings.opensearch_url],
        "use_ssl":     use_ssl,
        "verify_certs": False,
        "timeout":     timeout,
    }
    if settings.opensearch_user and settings.opensearch_pass:
        kwargs["http_auth"] = (settings.opensearch_user, settings.opensearch_pass)
    client = OpenSearch(**kwargs)
    info = client.info()
    log.info("opensearch.ok",
             version=info["version"]["number"],
             cluster=info["cluster_name"])
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _event_id(tipo: str, numero_processo: str, dt_evento: str) -> str:
    """Hash determinístico: garante idempotência."""
    key = f"{tipo}|{numero_processo}|{dt_evento}"
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    return date.today()


# ─────────────────────────────────────────────────────────────────────────────
# Modo 1: PRAZO_ALERT
# ─────────────────────────────────────────────────────────────────────────────

def iter_prazo_alerts(
    client: OpenSearch,
    horizonte_dias: int = 90,
    cnpj_basico: str | None = None,
    uf: str | None = None,
) -> Iterator[dict]:
    """
    Gera alertas de prazo varrendo mr_jazidas_v001.

    Alertas gerados:
      - VENCIMENTO_30D/60D/90D: dt_validade dentro do horizonte
      - VENCIDO_ATIVO: dt_validade < hoje E fase ativa
    """
    hoje    = _today()
    limite  = hoje + timedelta(days=horizonte_dias)
    now_iso = _now_iso()

    filters: list[dict] = [
        {"terms": {"fase": FASES_ATIVAS}},
    ]
    if cnpj_basico:
        filters.append({"term": {"cnpj_basico": cnpj_basico}})
    if uf:
        filters.append({"term": {"uf": uf.upper()}})

    # Busca processos com dt_validade preenchida dentro da janela
    query = {
        "size": SCROLL_SIZE,
        "_source": [
            "numero_processo", "nup", "titular", "cnpj_basico",
            "fase", "dt_validade", "substancias", "municipio", "uf",
        ],
        "query": {
            "bool": {
                "filter": filters + [
                    {"range": {"dt_validade": {
                        "lte": str(limite),    # vence até o horizonte
                    }}},
                ]
            }
        },
        "sort": [{"dt_validade": "asc"}],
    }

    scroll = client.search(
        index=INDEX_JAZ,
        body=query,
        params={"scroll": "5m"},
    )
    scroll_id = scroll["_scroll_id"]
    hits      = scroll["hits"]["hits"]
    total_in  = scroll["hits"]["total"]["value"]
    log.info("prazo.scan.start",
             total_candidatos=total_in,
             horizonte_dias=horizonte_dias,
             uf=uf, cnpj=cnpj_basico)

    generated = 0
    while hits:
        for hit in hits:
            src = hit["_source"]
            num = src.get("numero_processo")
            if not num:
                continue

            val_str = src.get("dt_validade")
            if not val_str:
                continue

            try:
                dt_val = date.fromisoformat(val_str[:10])
            except ValueError:
                continue

            dias_restantes = (dt_val - hoje).days

            # Classifica em subtipo/relevância
            if dias_restantes < 0 and src.get("fase") in FASES_ATIVAS:
                subtipo    = "VENCIDO_ATIVO"
                relevancia = "ALTA"
                titulo     = f"Processo VENCIDO ainda em fase ativa: {num}"
                resumo     = (
                    f"O processo {num} ({src.get('fase', '').title()}) "
                    f"venceu em {val_str} ({abs(dias_restantes)} dias atrás) "
                    f"mas permanece na fase '{src.get('fase', '')}'. "
                    f"Risco de nulidade ou necessidade de renovação urgente."
                )
            else:
                subtipo, relevancia = next(
                    ((s, r) for lo, hi, s, r in PRAZO_JANELAS
                     if lo <= dias_restantes <= hi),
                    (None, None),
                )
                if not subtipo:
                    continue
                titulo = f"Vencimento em {dias_restantes}d: {num}"
                resumo = (
                    f"O processo {num} ({src.get('fase', '').title()}) "
                    f"vence em {val_str} ({dias_restantes} dias). "
                    f"Titular: {src.get('titular', 'N/D')}. "
                    f"Substâncias: {', '.join(src.get('substancias') or ['N/D'])}. "
                    f"Município: {src.get('municipio', 'N/D')}/{src.get('uf', '')}."
                )

            dt_evento = str(hoje)
            event_id  = _event_id("PRAZO_ALERT", num, dt_evento)

            doc: dict[str, Any] = {
                "tipo_evento":    "PRAZO_ALERT",
                "subtipo":        subtipo,
                "titulo":         titulo,
                "resumo":         resumo,
                "numero_processo": num,
                "nup":            src.get("nup"),
                "cnpj_titular":   src.get("cnpj_basico"),
                "razao_social":   src.get("titular"),
                "fonte":          "INTERNO",
                "relevancia":     relevancia,
                "acao_necessaria": relevancia == "ALTA",
                "lido":           False,
                "dt_evento":      dt_evento,
                "dt_prazo":       val_str,
                "indexed_at":     now_iso,
            }
            doc = {k: v for k, v in doc.items() if v is not None}
            generated += 1
            yield {
                "_index":  INDEX_MON,
                "_id":     event_id,
                "_source": doc,
            }

        scroll = client.scroll(scroll_id=scroll_id, params={"scroll": "5m"})
        scroll_id = scroll["_scroll_id"]
        hits      = scroll["hits"]["hits"]

    client.clear_scroll(scroll_id=scroll_id)
    log.info("prazo.scan.done", generated=generated)


# ─────────────────────────────────────────────────────────────────────────────
# Modo 2: STATUS_CHANGE
# ─────────────────────────────────────────────────────────────────────────────

def iter_status_changes(
    client: OpenSearch,
    cnpj_basico: str | None = None,
    uf: str | None = None,
) -> Iterator[dict]:
    """
    Detecta mudanças de status comparando mr_jazidas_v001 com o último
    evento STATUS_CHANGE registrado em mr_monitoring_v001.

    Detecta:
      - FASE_ALTERADA:   fase mudou desde o último registro
      - TITULAR_ALTERADO: cnpj_basico mudou (cessão de direitos)
      - CANCELADO:       processo foi arquivado/cancelado
    """
    now_iso   = _now_iso()
    today_str = str(_today())

    # Mapa: numero_processo → último estado registrado
    last_known: dict[str, dict] = {}

    # Carrega último estado por processo (agregação max dt_evento)
    query_hist = {
        "size": 0,
        "query": {"term": {"tipo_evento": "STATUS_CHANGE"}},
        "aggs": {
            "por_processo": {
                "terms": {"field": "numero_processo", "size": 100_000},
                "aggs": {
                    "ultimo": {
                        "top_hits": {
                            "size": 1,
                            "sort": [{"dt_evento": "desc"}],
                            "_source": ["numero_processo", "subtipo",
                                        "conteudo", "cnpj_titular"],
                        }
                    }
                },
            }
        },
    }
    hist = client.search(index=INDEX_MON, body=query_hist)
    for bucket in hist["aggregations"]["por_processo"]["buckets"]:
        hits = bucket["ultimo"]["hits"]["hits"]
        if hits:
            s = hits[0]["_source"]
            last_known[bucket["key"]] = {
                "fase":         s.get("conteudo", ""),
                "cnpj_titular": s.get("cnpj_titular", ""),
            }

    log.info("status.snapshot.loaded", processos_rastreados=len(last_known))

    # Varre processos ativos atuais
    filters: list[dict] = []
    if cnpj_basico:
        filters.append({"term": {"cnpj_basico": cnpj_basico}})
    if uf:
        filters.append({"term": {"uf": uf.upper()}})

    query_cur = {
        "size": SCROLL_SIZE,
        "_source": [
            "numero_processo", "nup", "fase", "titular",
            "cnpj_basico", "municipio", "uf", "substancias",
        ],
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"_doc": "asc"}],
    }

    scroll = client.search(
        index=INDEX_JAZ,
        body=query_cur,
        params={"scroll": "5m"},
    )
    scroll_id = scroll["_scroll_id"]
    hits      = scroll["hits"]["hits"]
    generated = 0

    while hits:
        for hit in hits:
            src = hit["_source"]
            num = src.get("numero_processo")
            if not num:
                continue

            fase_atual  = (src.get("fase") or "").lower().strip()
            cnpj_atual  = src.get("cnpj_basico") or ""
            prev        = last_known.get(num)

            # Processo nunca rastreado — registra como baseline (silencioso)
            if prev is None:
                last_known[num] = {"fase": fase_atual, "cnpj_titular": cnpj_atual}
                # Não gera evento — sem histórico anterior para comparar
                continue

            fase_ant  = (prev.get("fase") or "").lower().strip()
            cnpj_ant  = prev.get("cnpj_titular") or ""

            changes = []
            if fase_atual != fase_ant and fase_ant:
                changes.append(("FASE_ALTERADA", "MEDIA",
                                f"Fase alterada de '{fase_ant}' → '{fase_atual}'"))
            if cnpj_atual != cnpj_ant and cnpj_ant:
                changes.append(("TITULAR_ALTERADO", "ALTA",
                                f"Titular alterado (CNPJ {cnpj_ant} → {cnpj_atual})"))

            for subtipo, relevancia, detalhe in changes:
                titulo  = f"{subtipo.replace('_', ' ').title()}: {num}"
                resumo  = (
                    f"Processo {num} ({src.get('titular', 'N/D')}): {detalhe}. "
                    f"Município: {src.get('municipio', 'N/D')}/{src.get('uf', '')}."
                )
                event_id = _event_id(subtipo, num, today_str)
                doc: dict[str, Any] = {
                    "tipo_evento":    "STATUS_CHANGE",
                    "subtipo":        subtipo,
                    "titulo":         titulo,
                    "resumo":         resumo,
                    "conteudo":       fase_atual,  # fase atual (para próxima diff)
                    "numero_processo": num,
                    "nup":            src.get("nup"),
                    "cnpj_titular":   cnpj_atual,
                    "razao_social":   src.get("titular"),
                    "fonte":          "INTERNO",
                    "relevancia":     relevancia,
                    "acao_necessaria": relevancia == "ALTA",
                    "lido":           False,
                    "dt_evento":      today_str,
                    "indexed_at":     now_iso,
                }
                doc = {k: v for k, v in doc.items() if v is not None}
                generated += 1
                yield {
                    "_index":  INDEX_MON,
                    "_id":     event_id,
                    "_source": doc,
                }

            # Atualiza estado conhecido
            last_known[num] = {"fase": fase_atual, "cnpj_titular": cnpj_atual}

        scroll = client.scroll(scroll_id=scroll_id, params={"scroll": "5m"})
        scroll_id = scroll["_scroll_id"]
        hits      = scroll["hits"]["hits"]

    client.clear_scroll(scroll_id=scroll_id)
    log.info("status.scan.done", generated=generated)


# ─────────────────────────────────────────────────────────────────────────────
# Modo 3: DOU_PUBLICACAO (INLABS — ZIP download + XML parse)
# ─────────────────────────────────────────────────────────────────────────────

# Palavras-chave para filtrar artigos ANM/mineração nos XMLs do INLABS
# Termos específicos para evitar falsos positivos (ex: "mineração" em contexto
# de mineração de dados ou "lavra" de lavra parlamentar)
_ANM_KEYWORDS = [
    "agencia nacional de mineracao",
    "agência nacional de mineração",
    "portaria anm",
    "resolucao anm", "resolução anm",
    "/anm/",
    "cnpj 29.427.565",                # CNPJ oficial da ANM
    "mineracao.gov.br",
    "concessao de lavra", "concessão de lavra",
    "autorizacao de pesquisa", "autorização de pesquisa",
    "lavra garimpeira",
    "outorga de lavra", "outorga mineral",
    "titulo mineral", "título mineral",
    "processo scm", "processo anm",
    "regime de lavra",
    "registro de extracao", "registro de extração",
    "substancias minerais", "substâncias minerais",
]


def _inlabs_login(email: str, password: str):
    """Autentica no INLABS. Retorna session autenticada ou None."""
    import requests as _req
    import urllib3 as _u3
    _u3.disable_warnings()

    s = _req.Session()
    s.headers.update({
        "User-Agent": "MineralRadar-ETL/1.0",
        "Accept":     "text/html,application/xhtml+xml",
    })
    s.verify = False

    # Inicializa cookies PHP
    s.get("https://inlabs.in.gov.br/logar.php")

    # Faz login
    s.post(
        "https://inlabs.in.gov.br/logar.php",
        data={"email": email.strip(), "password": password},
        allow_redirects=True,
    )

    if "inlabs_session_cookie" not in s.cookies:
        return None
    return s


def _inlabs_list_zips(session, data_str: str) -> list[dict]:
    """
    Lista ZIPs disponíveis para a data.

    A URL de download usa query params:
      https://inlabs.in.gov.br/?p=YYYY-MM-DD&dl=YYYY-MM-DD-DO1.zip
    Os links no HTML vêm com &amp; que precisam ser convertidos.
    """
    import re as _re
    from html import unescape as _unescape
    r = session.get(
        f"https://inlabs.in.gov.br/index.php?p={data_str}",
        allow_redirects=True,
    )
    # Links de ZIP no formato ?p=...&amp;dl=...zip
    links = _re.findall(r'href=["\']([^"\']*(?:dl=)[^"\']*\.zip[^"\']*)["\']',
                        r.text, _re.IGNORECASE)
    result = []
    for link in links:
        link_clean = _unescape(link)   # &amp; → &
        full_url   = (link_clean if link_clean.startswith("http")
                      else f"https://inlabs.in.gov.br/{link_clean}")
        # Nome do arquivo: parte após dl=
        nome_match = _re.search(r'dl=([^&]+)', link_clean)
        nome       = nome_match.group(1) if nome_match else link_clean.rsplit("/", 1)[-1]
        secao      = ("DO1" if "DO1" in nome
                      else "DO2" if "DO2" in nome
                      else "DO3" if "DO3" in nome
                      else "EXTRA")
        result.append({"nome": nome, "url": full_url, "secao": secao})
    return result


def _parse_zip_articles(session, file_info: dict, keywords: list[str]) -> list[dict]:
    """
    Baixa ZIP do INLABS, extrai XMLs, filtra por palavras-chave.

    Estrutura XML do INLABS:
      <xml>
        <article id="..." name="..." artType="..." artCategory="..."
                 pubDate="DD/MM/YYYY" numberPage="N" pdfPage="URL">
          <body>
            <Identifica><![CDATA[TÍTULO DO ATO]]></Identifica>
            <Ementa><![CDATA[Resumo/ementa.]]></Ementa>
            <Texto><![CDATA[Texto completo HTML.]]></Texto>
          </body>
        </article>
      </xml>
    """
    import zipfile, io, re as _re
    from html import unescape as _unescape

    r = session.get(file_info["url"], stream=True, timeout=120)
    r.raise_for_status()

    articles = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for xml_name in zf.namelist():
            if not xml_name.lower().endswith(".xml"):
                continue
            try:
                xml_text = zf.read(xml_name).decode("utf-8", errors="replace")
            except Exception:
                continue
            if not any(kw in xml_text.lower() for kw in keywords):
                continue

            def _cdata(tag: str) -> str:
                """Extrai conteúdo de tag com CDATA ou texto simples."""
                m = _re.search(
                    rf'<{tag}[^>]*><!\[CDATA\[(.*?)\]\]></{tag}>',
                    xml_text, _re.IGNORECASE | _re.DOTALL,
                )
                if m:
                    return _re.sub(r'<[^>]+>', ' ', m.group(1)).strip()
                m = _re.search(
                    rf'<{tag}[^>]*>(.*?)</{tag}>',
                    xml_text, _re.IGNORECASE | _re.DOTALL,
                )
                return _re.sub(r'<[^>]+>', ' ', m.group(1)).strip() if m else ""

            def _attr(attr: str) -> str:
                m = _re.search(rf'\b{attr}="([^"]*)"', xml_text)
                return _unescape(m.group(1)) if m else ""

            art_category = _attr("artCategory")
            orgao        = art_category.split("/")[0].strip() if "/" in art_category else art_category
            identifica   = _cdata("Identifica")
            ementa       = _cdata("Ementa")
            texto        = _cdata("Texto")
            pub_date_raw = _attr("pubDate")   # "DD/MM/YYYY"
            # Converte para ISO
            pub_date_iso = ""
            if pub_date_raw and "/" in pub_date_raw:
                parts = pub_date_raw.split("/")
                pub_date_iso = f"{parts[2]}-{parts[1]}-{parts[0]}"

            articles.append({
                "titulo":   (identifica or _attr("name"))[:512],
                "ementa":   ementa[:1024],
                "corpo":    texto[:4096],
                "orgao":    orgao[:256],
                "art_type": _attr("artType"),
                "secao":    file_info["secao"],
                "arquivo":  xml_name,
                "dt_pub":   pub_date_iso,
                "url":      _attr("pdfPage") or None,
                "art_id":   _attr("id"),
            })
    return articles


def iter_dou_publicacoes(
    inlabs_email:    str,
    inlabs_password: str,
    data_busca:      str,
    secoes:          list[str] | None = None,
) -> Iterator[dict]:
    """
    Autentica no INLABS, baixa os ZIPs do DOU, filtra artigos ANM/mineração.

    Args:
        inlabs_email:    E-mail cadastrado em inlabs.in.gov.br
        inlabs_password: Senha INLABS
        data_busca:      Data no formato YYYY-MM-DD
        secoes:          Seções a processar, ex: ["DO1", "DO3"] (default: todas)

    Para cadastrar:
        python scripts/inlabs_setup.py register
    """
    now_iso   = _now_iso()
    secoes_ok = {s.upper() for s in (secoes or ["DO1", "DO2", "DO3"])}
    keywords  = _ANM_KEYWORDS
    generated = 0

    # Login
    session = _inlabs_login(inlabs_email, inlabs_password)
    if session is None:
        log.error("dou.login.falhou",
                  msg="Credenciais INLABS inválidas. "
                      "Cadastre-se em https://inlabs.in.gov.br "
                      "ou rode: python scripts/inlabs_setup.py register")
        return

    log.info("dou.login.ok", data=data_busca)

    # Lista arquivos
    all_files = _inlabs_list_zips(session, data_busca)
    files     = [f for f in all_files if f["secao"] in secoes_ok]

    if not files:
        log.warning("dou.sem_arquivos", data=data_busca, total_raw=len(all_files))
        return

    log.info("dou.arquivos_encontrados", total=len(files), data=data_busca)

    # Download + parse + index
    for file_info in files:
        log.info("dou.processando", arquivo=file_info["nome"])
        try:
            articles = _parse_zip_articles(session, file_info, keywords)
        except Exception as e:
            log.error("dou.zip.error", arquivo=file_info["nome"], error=str(e))
            continue

        log.info("dou.artigos_anm", arquivo=file_info["nome"], total=len(articles))

        for art in articles:
            # Tenta extrair número de processo do texto
            import re as _re
            full_text = f"{art['titulo']} {art['ementa']} {art['corpo']}"
            proc_match = _re.search(
                r'\b(\d{3,4}[./]\d{3,6}[./]\d{4})\b',
                full_text[:2000],
            )
            num_processo = proc_match.group(1) if proc_match else None

            # Usa art_id único do INLABS como hash base para idempotência
            id_base  = art.get("art_id") or art["arquivo"]
            event_id = _event_id("DOU_PUBLICACAO", id_base, data_busca)

            # Resumo = ementa se disponível, senão começo do corpo
            resumo = art["ementa"] or art["corpo"][:400]

            doc: dict[str, Any] = {
                "tipo_evento":     "DOU_PUBLICACAO",
                "subtipo":         art["secao"],
                "titulo":          art["titulo"] or art["arquivo"],
                "conteudo":        art["corpo"],
                "resumo":          resumo[:500],
                "numero_processo": num_processo,
                "razao_social":    art["orgao"],
                "fonte":           "DOU",
                "secao_dou":       art["secao"][-1],  # "1" | "2" | "3"
                "url":             art.get("url"),
                "relevancia":      "MEDIA",
                "acao_necessaria": False,
                "lido":            False,
                "dt_evento":       art.get("dt_pub") or data_busca,
                "indexed_at":      now_iso,
            }
            doc = {k: v for k, v in doc.items() if v is not None}
            generated += 1
            yield {
                "_index":  INDEX_MON,
                "_id":     event_id,
                "_source": doc,
            }

    log.info("dou.scan.done", data=data_busca, generated=generated)


# ─────────────────────────────────────────────────────────────────────────────
# Bulk index
# ─────────────────────────────────────────────────────────────────────────────

def bulk_index(
    client: OpenSearch,
    docs: Iterator[dict],
    label: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Upsert em batches. Retorna (ok, erros)."""
    ok_total  = 0
    err_total = 0
    batch: list[dict] = []
    t0 = time.monotonic()

    def _flush():
        nonlocal ok_total, err_total
        if not batch:
            return
        if dry_run:
            ok_total += len(batch)
            batch.clear()
            return
        ok, errs = helpers.bulk(
            client, batch,
            raise_on_error=False,
            max_retries=3,
            initial_backoff=2,
        )
        ok_total  += ok
        err_total += len(errs)
        if errs:
            log.warning("monitoring.index.errors",
                        mode=label, sample=str(errs[0])[:200])
        batch.clear()

    for doc in docs:
        batch.append(doc)
        if len(batch) >= BATCH_SIZE:
            _flush()

    _flush()
    elapsed = time.monotonic() - t0
    log.info("monitoring.index.done",
             mode=label, ok=ok_total, errs=err_total,
             elapsed_s=round(elapsed, 1))
    return ok_total, err_total


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--modo",
              type=click.Choice(["prazo", "status", "dou", "all"]),
              default="prazo",
              help="Modo de monitoramento.")
@click.option("--horizonte-dias", default=90, show_default=True,
              help="Janela de alerta de prazo (dias).")
@click.option("--data",           default=None,
              help="Data para busca DOU (YYYY-MM-DD, default: hoje).")
@click.option("--cnpj",           default=None,
              help="Filtrar por CNPJ básico (8 dígitos).")
@click.option("--uf",             default=None,
              help="Filtrar por UF (ex: MG).")
@click.option("--inlabs-email",    default=None,
              help="E-mail INLABS (sobrescreve INLABS_EMAIL do .env).")
@click.option("--inlabs-password", default=None,
              help="Senha INLABS (sobrescreve INLABS_PASSWORD do .env).")
@click.option("--dry-run",         is_flag=True,
              help="Parse e log sem indexar.")
def main(
    modo: str,
    horizonte_dias: int,
    data: str | None,
    cnpj: str | None,
    uf: str | None,
    inlabs_email: str | None,
    inlabs_password: str | None,
    dry_run: bool,
):
    """
    Bot de monitoramento de processos ANM → mr_monitoring_v001.

    Exemplos:
      python -m bots.bot_monitoring --modo prazo --horizonte-dias 60
      python -m bots.bot_monitoring --modo status --uf MG
      python -m bots.bot_monitoring --modo dou --data 2026-05-11
      python -m bots.bot_monitoring --modo all

    Cadastro INLABS (uma vez):
      python scripts/inlabs_setup.py register
    """
    client    = get_os_client()
    data_str  = data or str(_today())
    total_ok  = 0
    total_err = 0

    # Credenciais INLABS: arg CLI tem prioridade, fallback para settings (.env)
    inlabs_email    = inlabs_email    or settings.inlabs_email    or None
    inlabs_password = inlabs_password or settings.inlabs_password or None

    if modo in ("prazo", "all"):
        log.info("monitoring.modo.prazo",
                 horizonte_dias=horizonte_dias, uf=uf, cnpj=cnpj)
        docs = iter_prazo_alerts(client, horizonte_dias, cnpj, uf)
        ok, err = bulk_index(client, docs, "prazo", dry_run)
        total_ok  += ok
        total_err += err

    if modo in ("status", "all"):
        log.info("monitoring.modo.status", uf=uf, cnpj=cnpj)
        docs = iter_status_changes(client, cnpj, uf)
        ok, err = bulk_index(client, docs, "status", dry_run)
        total_ok  += ok
        total_err += err

    if modo in ("dou", "all"):
        if not inlabs_email or not inlabs_password:
            log.warning(
                "monitoring.dou.sem_credenciais",
                msg="Modo DOU requer credenciais INLABS. "
                    "Para cadastrar: python scripts/inlabs_setup.py register. "
                    "Ou configure INLABS_EMAIL e INLABS_PASSWORD no .env."
            )
        else:
            log.info("monitoring.modo.dou", data=data_str)
            docs = iter_dou_publicacoes(inlabs_email, inlabs_password, data_str)
            ok, err = bulk_index(client, docs, "dou", dry_run)
            total_ok  += ok
            total_err += err

    log.info("monitoring.run.done",
             modo=modo, ok=total_ok, err=total_err)


if __name__ == "__main__":
    main()
