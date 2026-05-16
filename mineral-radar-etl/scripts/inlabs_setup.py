#!/usr/bin/env python3
"""
inlabs_setup.py — Cadastro, login e teste da API INLABS (Imprensa Nacional)
==============================================================================

Uso:
    # Cadastro novo + salva credenciais no .env
    python scripts/inlabs_setup.py register

    # Só testa login e lista ZIPs do dia
    python scripts/inlabs_setup.py login

    # Testa busca ANM numa data específica
    python scripts/inlabs_setup.py test --data 2026-05-09

    # Lista arquivos disponíveis para uma data
    python scripts/inlabs_setup.py list --data 2026-05-09

O INLABS é o portal oficial de dados abertos do Diário Oficial da União,
mantido pela Imprensa Nacional (https://inlabs.in.gov.br).

Após o cadastro, as credenciais são salvas no .env como:
    INLABS_EMAIL=seu@email.com
    INLABS_PASSWORD=sua_senha

O bot_monitoring.py --modo dou usa essas credenciais automaticamente.
"""
from __future__ import annotations

import getpass
import os
import re
import sys
import zipfile
import io
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import click
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INLABS_BASE   = "https://inlabs.in.gov.br/"
ENV_FILE      = Path(__file__).parents[2] / ".env"          # MineralRadar/.env
ENV_FILE_ETL  = Path(__file__).parents[1] / ".env"          # mineral-radar-etl/.env

HEADERS = {
    "User-Agent":    "MineralRadar/1.0 (contato@mineralradar.com.br)",
    "Accept":        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type":  "application/x-www-form-urlencoded",
    "Referer":       INLABS_BASE,
}

# Palavras-chave para filtrar artigos ANM/mineração no XML
ANM_KEYWORDS = [
    "agencia nacional de mineracao", "anm", "mineracao", "lavra garimpeira",
    "autorizacao de pesquisa", "concessao de lavra", "licenciamento mineral",
    "substancias minerais", "processo anm", "outorga mineral",
    "regime de lavra", "registro de extracao", "portaria anm",
]


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.verify = False
    return s


def inlabs_register(
    session:       requests.Session,
    email:         str,
    password:      str,
    nome_completo: str,
    uf_cidade:     str,
    telefone:      str = "",
    nome_empresa:  str = "MineralRadar",
) -> tuple[bool, str]:
    """Registra novo usuário no INLABS. Retorna (sucesso, mensagem)."""
    # Inicializa sessão PHP
    session.get(urljoin(INLABS_BASE, "logar.php"))

    r = session.post(
        urljoin(INLABS_BASE, "registrar.php"),
        data={
            "email":         email.strip(),
            "password":      password,
            "password2":     password,
            "nome_completo": nome_completo.strip(),
            "telefone":      telefone.strip(),
            "uf_cidade":     uf_cidade.strip(),
            "nome_empresa":  nome_empresa.strip(),
        },
        allow_redirects=True,
    )

    text = r.text

    # Procura mensagens de erro ou sucesso na página
    msgs_err = re.findall(
        r'alert-danger[^>]*>\s*<p>([^<]+)</p>',
        text, re.IGNORECASE,
    )
    msgs_ok = re.findall(
        r'alert-success[^>]*>\s*<p>([^<]+)</p>',
        text, re.IGNORECASE,
    )

    if msgs_err:
        return False, " | ".join(msgs_err)
    if msgs_ok:
        return True, " | ".join(msgs_ok)

    # Verifica se cookie de sessão foi criado
    if "inlabs_session_cookie" in session.cookies:
        return True, "Cadastro realizado e login automático efetuado."

    # Fallback: procura qualquer mensagem no HTML
    any_msg = re.findall(r'<h4[^>]*>(.*?)</h4>', text, re.IGNORECASE)
    return False, f"Resposta ambígua. HTML msgs: {any_msg[:3]}"


def inlabs_login(
    session:  requests.Session,
    email:    str,
    password: str,
) -> tuple[bool, str]:
    """Login no INLABS. Retorna (sucesso, mensagem)."""
    # Inicializa sessão
    session.get(urljoin(INLABS_BASE, "logar.php"))

    r = session.post(
        urljoin(INLABS_BASE, "logar.php"),
        data={"email": email.strip(), "password": password},
        allow_redirects=True,
    )

    if "inlabs_session_cookie" in session.cookies:
        cookie_val = session.cookies["inlabs_session_cookie"]
        return True, f"Login OK. Cookie: {cookie_val[:20]}..."

    # Analisa mensagem de erro
    msgs = re.findall(
        r'alert-danger[^>]*>\s*<p>([^<]+)</p>',
        r.text, re.IGNORECASE,
    )
    if msgs:
        return False, " | ".join(msgs)

    return False, "Cookie 'inlabs_session_cookie' não encontrado na resposta."


def inlabs_list_files(
    session:    requests.Session,
    data_str:   str,
) -> list[dict]:
    """
    Lista os arquivos ZIP disponíveis para uma data (YYYY-MM-DD).
    Retorna lista de {nome, url, secao}.

    Links no HTML: ?p=YYYY-MM-DD&amp;dl=YYYY-MM-DD-DO1.zip
    """
    from html import unescape as _unescape

    url = urljoin(INLABS_BASE, f"index.php?p={data_str}")
    r   = session.get(url, allow_redirects=True)

    if "inlabs_session_cookie" not in session.cookies:
        return []

    links = re.findall(
        r'href=["\']([^"\']*(?:dl=)[^"\']*\.zip[^"\']*)["\']',
        r.text, re.IGNORECASE,
    )
    result = []
    for link in links:
        link_clean = _unescape(link)
        full_url   = (link_clean if link_clean.startswith("http")
                      else urljoin(INLABS_BASE, link_clean))
        nome_match = re.search(r'dl=([^&]+)', link_clean)
        nome       = nome_match.group(1) if nome_match else link_clean.rsplit("/", 1)[-1]
        secao      = ("DO1" if "DO1" in nome
                      else "DO2" if "DO2" in nome
                      else "DO3" if "DO3" in nome
                      else "EXTRA")
        result.append({"nome": nome, "url": full_url, "secao": secao})

    return result


def download_and_parse_zip(
    session: requests.Session,
    file_info: dict,
    keywords: list[str] | None = None,
) -> list[dict]:
    """
    Baixa ZIP do INLABS, extrai XMLs, filtra por palavras-chave.

    Estrutura XML:
      <article artType="..." artCategory="..." pubDate="DD/MM/YYYY" pdfPage="URL">
        <body>
          <Identifica><![CDATA[TÍTULO]]></Identifica>
          <Ementa><![CDATA[Resumo.]]></Ementa>
          <Texto><![CDATA[Texto HTML completo.]]></Texto>
        </body>
      </article>
    """
    from html import unescape as _unescape

    kws = [k.lower() for k in (keywords or ANM_KEYWORDS)]
    r   = session.get(file_info["url"], stream=True, timeout=120)
    r.raise_for_status()

    articles: list[dict] = []

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for xml_name in zf.namelist():
            if not xml_name.lower().endswith(".xml"):
                continue
            try:
                xml_text = zf.read(xml_name).decode("utf-8", errors="replace")
            except Exception:
                continue

            if not any(kw in xml_text.lower() for kw in kws):
                continue

            def _cdata(tag: str) -> str:
                m = re.search(
                    rf'<{tag}[^>]*><!\[CDATA\[(.*?)\]\]></{tag}>',
                    xml_text, re.IGNORECASE | re.DOTALL,
                )
                if m:
                    return re.sub(r'<[^>]+>', ' ', m.group(1)).strip()
                m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', xml_text,
                              re.IGNORECASE | re.DOTALL)
                return re.sub(r'<[^>]+>', ' ', m.group(1)).strip() if m else ""

            def _attr(attr: str) -> str:
                m = re.search(rf'\b{attr}="([^"]*)"', xml_text)
                return _unescape(m.group(1)) if m else ""

            cat     = _attr("artCategory")
            orgao   = cat.split("/")[0].strip() if "/" in cat else cat
            pub_raw = _attr("pubDate")
            pub_iso = ""
            if pub_raw and "/" in pub_raw:
                p = pub_raw.split("/")
                pub_iso = f"{p[2]}-{p[1]}-{p[0]}"

            articles.append({
                "titulo":   _cdata("Identifica")[:512] or _attr("name"),
                "ementa":   _cdata("Ementa")[:1024],
                "corpo":    _cdata("Texto")[:4096],
                "orgao":    orgao[:256],
                "art_type": _attr("artType"),
                "secao":    file_info["secao"],
                "arquivo":  xml_name,
                "dt_pub":   pub_iso,
                "url":      _attr("pdfPage") or None,
            })

    return articles


# ─────────────────────────────────────────────────────────────────────────────
# .env helpers
# ─────────────────────────────────────────────────────────────────────────────

def _update_env(env_path: Path, key: str, value: str):
    """Atualiza ou adiciona uma variável no arquivo .env."""
    if not env_path.exists():
        env_path.touch()

    content = env_path.read_text()
    pattern = rf'^{re.escape(key)}\s*=.*$'

    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{key}={value}\n"

    env_path.write_text(content)
    click.echo(f"  ✓ {key} atualizado em {env_path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Gerenciamento de acesso INLABS (Diário Oficial da União)."""
    pass


@cli.command()
def register():
    """Cadastra novo usuário no INLABS e salva credenciais no .env."""
    click.echo("\n=== Cadastro INLABS — Portal Dados Abertos DOU ===")
    click.echo("Serviço gratuito da Imprensa Nacional para acesso programático ao DOU.\n")

    email         = click.prompt("E-mail (será seu login)")
    password      = getpass.getpass("Senha (min. 6 chars): ")
    nome_completo = click.prompt("Nome completo")
    uf_cidade     = click.prompt("UF/Cidade", default="MG/Belo Horizonte")
    telefone      = click.prompt("Telefone com DDD (opcional)", default="", show_default=False)
    nome_empresa  = click.prompt("Nome da empresa (opcional)", default="MineralRadar")

    click.echo("\nRegistrando...")
    s = make_session()
    ok, msg = inlabs_register(
        s, email, password, nome_completo, uf_cidade, telefone, nome_empresa
    )

    if not ok:
        click.echo(f"\n✘ Erro no cadastro: {msg}")
        click.echo("\nSe o e-mail já está cadastrado, tente o comando 'login'.")
        sys.exit(1)

    click.echo(f"\n✔ {msg}")

    # Testa login imediatamente
    click.echo("\nTestando login...")
    s2 = make_session()
    logged, login_msg = inlabs_login(s2, email, password)
    if not logged:
        click.echo(f"⚠ Cadastro OK mas login falhou: {login_msg}")
        click.echo("Aguarde alguns minutos e tente: python scripts/inlabs_setup.py login")
    else:
        click.echo(f"✔ {login_msg}")

    # Salva no .env
    click.echo("\nSalvando credenciais no .env...")
    for env_path in [ENV_FILE, ENV_FILE_ETL]:
        if env_path.exists():
            _update_env(env_path, "INLABS_EMAIL",    email)
            _update_env(env_path, "INLABS_PASSWORD", password)

    click.echo("\n✔ Tudo pronto! Para testar a busca ANM:")
    click.echo("   python scripts/inlabs_setup.py test")
    click.echo("\nPara rodar o monitoramento DOU:")
    click.echo("   python -m bots.bot_monitoring --modo dou")


@cli.command()
def login():
    """Testa login com credenciais existentes no .env."""
    # Tenta ler do .env
    email    = os.environ.get("INLABS_EMAIL", "")
    password = os.environ.get("INLABS_PASSWORD", "")

    if not email:
        email = click.prompt("E-mail INLABS")
    if not password:
        password = getpass.getpass(f"Senha INLABS para {email}: ")

    click.echo(f"\nTestando login para {email}...")
    s = make_session()
    ok, msg = inlabs_login(s, email, password)

    if not ok:
        click.echo(f"✘ Falha: {msg}")
        sys.exit(1)

    click.echo(f"✔ {msg}")

    # Lista arquivos de hoje
    hoje = str(date.today())
    click.echo(f"\nListando arquivos disponíveis para {hoje}...")
    files = inlabs_list_files(s, hoje)
    if not files:
        # Tenta ontem (DOU não publica aos fins de semana)
        ontem = str(date.today() - timedelta(days=1))
        click.echo(f"Nenhum arquivo hoje. Tentando {ontem}...")
        files = inlabs_list_files(s, ontem)

    if files:
        click.echo(f"✔ {len(files)} arquivo(s) encontrado(s):")
        for f in files:
            click.echo(f"   [{f['secao']:5s}] {f['nome']}")
    else:
        click.echo("⚠ Nenhum arquivo encontrado. Verifique a data ou tente amanhã.")


@cli.command()
@click.option("--data", default=None, help="Data no formato YYYY-MM-DD (default: último dia útil)")
@click.option("--secao", default="DO1", help="Seção do DOU: DO1, DO2, DO3, EXTRA, TODOS")
@click.option("--salvar", is_flag=True, help="Salva artigos encontrados em /tmp/inlabs_artigos.json")
def test(data: str | None, secao: str, salvar: bool):
    """Testa download e filtragem de publicações ANM no DOU."""
    email    = os.environ.get("INLABS_EMAIL", "")
    password = os.environ.get("INLABS_PASSWORD", "")

    if not email:
        email = click.prompt("E-mail INLABS")
    if not password:
        password = getpass.getpass(f"Senha INLABS para {email}: ")

    s  = make_session()
    ok, msg = inlabs_login(s, email, password)
    if not ok:
        click.echo(f"✘ Login falhou: {msg}")
        sys.exit(1)
    click.echo(f"✔ Autenticado.")

    # Determina data
    if not data:
        # Busca o último dia com publicação (volta até 5 dias)
        for delta in range(0, 6):
            data = str(date.today() - timedelta(days=delta))
            files = inlabs_list_files(s, data)
            if files:
                click.echo(f"Data com publicações: {data}")
                break
    else:
        files = inlabs_list_files(s, data)

    if not files:
        click.echo(f"✘ Nenhum arquivo encontrado para {data}.")
        sys.exit(1)

    # Filtra por seção
    secao_upper = secao.upper()
    if secao_upper != "TODOS":
        files_filtrados = [f for f in files if f["secao"] == secao_upper]
    else:
        files_filtrados = files

    click.echo(f"\n{len(files_filtrados)} arquivo(s) na seção {secao}:")
    for f in files_filtrados:
        click.echo(f"  {f['nome']}")

    # Download e parse
    all_articles: list[dict] = []
    for f in files_filtrados:
        click.echo(f"\nBaixando e filtrando {f['nome']}...")
        try:
            arts = download_and_parse_zip(s, f)
            click.echo(f"  → {len(arts)} artigo(s) ANM encontrado(s)")
            all_articles.extend(arts)
        except Exception as e:
            click.echo(f"  ✘ Erro: {e}")

    click.echo(f"\n{'='*50}")
    click.echo(f"Total: {len(all_articles)} artigo(s) ANM/mineração em {data} / {secao}")

    for art in all_articles[:5]:
        click.echo(f"\n  [{art['secao']}] {art.get('art_type', '')} | {art['orgao'] or 'Órgão N/D'}")
        click.echo(f"  Título: {art['titulo'][:100]}")
        click.echo(f"  Ementa: {art.get('ementa', '')[:150]}")
        if art.get("url"):
            click.echo(f"  PDF:    {art['url'][:80]}")

    if salvar and all_articles:
        import json
        out = Path("/tmp/inlabs_artigos.json")
        out.write_text(json.dumps(all_articles, ensure_ascii=False, indent=2))
        click.echo(f"\n✔ Artigos salvos em {out}")


@cli.command("list")
@click.option("--data", default=None, help="Data YYYY-MM-DD (default: hoje)")
def list_files(data: str | None):
    """Lista arquivos ZIP disponíveis no INLABS para uma data."""
    email    = os.environ.get("INLABS_EMAIL", "")
    password = os.environ.get("INLABS_PASSWORD", "")
    if not email:
        email = click.prompt("E-mail INLABS")
    if not password:
        password = getpass.getpass(f"Senha INLABS para {email}: ")

    s = make_session()
    ok, msg = inlabs_login(s, email, password)
    if not ok:
        click.echo(f"✘ {msg}")
        sys.exit(1)

    data = data or str(date.today())
    files = inlabs_list_files(s, data)

    if not files:
        click.echo(f"Nenhum arquivo para {data} (provável fim de semana ou feriado).")
        return

    click.echo(f"\nArquivos INLABS — {data}:")
    for f in files:
        click.echo(f"  [{f['secao']:5s}] {f['nome']:<50s}  {f['url']}")


if __name__ == "__main__":
    cli()
