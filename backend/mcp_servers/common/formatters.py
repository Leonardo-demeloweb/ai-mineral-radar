"""
Shared Formatting Utilities
============================

Reusable formatting helpers used across MCP servers (Jazidas, Empresas).

- CNPJ formatting
- Contact/phone formatting
- Address formatting (inline)
- Municipality name extraction (rfb_cnpj_v003 variants)
"""

from typing import Any


def only_digits(value: str | None) -> str:
    """Extract ASCII digits from a string (CNPJ/CPF/código misturado com máscara)."""
    if not value:
        return ""
    return "".join(c for c in str(value) if c.isdigit())


def digitos_verificadores_cnpj12(base8: str, ordem4: str) -> tuple[str, str] | None:
    """
    Calcula os dois dígitos verificadores de um CNPJ a partir dos 12 primeiros dígitos
    (8 da raiz + 4 do estabelecimento), regra da Receita Federal.
    """
    b = only_digits(base8).zfill(8)
    o = only_digits(ordem4).zfill(4)
    if len(b) != 8 or len(o) != 4:
        return None
    d = [int(x) for x in b + o]
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s1 = sum(d[i] * w1[i] for i in range(12))
    r1 = s1 % 11
    dv1 = 0 if r1 < 2 else 11 - r1
    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d13 = d + [dv1]
    s2 = sum(d13[i] * w2[i] for i in range(13))
    r2 = s2 % 11
    dv2 = 0 if r2 < 2 else 11 - r2
    return str(dv1), str(dv2)


def format_cnpj_desde_basico_e_ordem(
    basico: str, ordem: str = "0001"
) -> str | None:
    """
    Monta o CNPJ completo formatado a partir da raiz (8 dígitos) e do estabelecimento (4).

    Usado quando a fonte ANM/SIGMINE só traz a raiz: assume-se ``ordem`` (default matriz
    ``0001``) para exibir um CNPJ válido algoritmicamente; o filial real na RFB pode ser outro.
    """
    dvs = digitos_verificadores_cnpj12(basico, ordem)
    if dvs is None:
        return None
    dv1, dv2 = dvs
    b = only_digits(basico).zfill(8)
    o = only_digits(ordem).zfill(4)
    return format_cnpj(b, o, f"{dv1}{dv2}")


def format_cnpj(basico: str, ordem: str = "", dv: str = "") -> str | None:
    """
    Format a full CNPJ: ``XX.XXX.XXX/YYYY-ZZ``.

    Args:
        basico: 8-digit base (e.g., "33592510")
        ordem: 4-digit branch order (e.g., "0001")
        dv: 2-digit check digits (e.g., "56")

    Returns:
        Formatted CNPJ or None if basico is empty.
    """
    if not basico:
        return None
    b = basico.zfill(8)
    o = ordem.zfill(4) if ordem else "0001"
    d = dv.zfill(2) if dv else "00"
    return f"{b[:2]}.{b[2:5]}.{b[5:8]}/{o}-{d}"


def build_telefone(ddd: str, numero: str) -> str | None:
    """Build phone string: ``(11) 25428111``."""
    ddd = str(ddd).strip() if ddd else ""
    numero = str(numero).strip() if numero else ""
    if ddd and numero:
        return f"({ddd}) {numero}"
    return numero or None


def build_contato(source: dict) -> dict[str, Any]:
    """
    Build contact dict from rfb_cnpj_v003 source fields.

    Expects keys: ddd1, telefone1, ddd2, telefone2, correioEletronico,
    tipoLogradouro, logradouro, numero, complemento, bairro, cep.
    """
    return {
        "telefone": build_telefone(
            source.get("ddd1", ""), source.get("telefone1", "")
        ),
        "telefone2": build_telefone(
            source.get("ddd2", ""), source.get("telefone2", "")
        ),
        "email": source.get("correioEletronico") or None,
        "endereco": build_endereco_inline(source) or None,
    }


def build_endereco_inline(source: dict) -> str:
    """Build inline address: ``AVENIDA PAULISTA, 671, SALA 4, BELA VISTA - CEP 01311100``.

    tipoLogradouro and logradouro are joined with a space (not a comma) so
    Azure Maps receives a proper street name like "AVENIDA PAULISTA" instead
    of the ambiguous "AVENIDA, PAULISTA" which confuses the geocoder.
    """
    parts = []

    # Concatenate street type + name as a single token: "AVENIDA PAULISTA"
    tipo = str(source.get("tipoLogradouro") or "").strip()
    logradouro = str(source.get("logradouro") or "").strip()
    if tipo and logradouro:
        parts.append(f"{tipo} {logradouro}")
    elif logradouro:
        parts.append(logradouro)
    elif tipo:
        parts.append(tipo)

    for campo in ("numero", "complemento", "bairro"):
        val = source.get(campo)
        if val and str(val).strip():
            parts.append(str(val).strip())

    endereco = ", ".join(parts)
    cep = source.get("cep", "")
    if cep:
        endereco += f" - CEP {cep}"
    return endereco


def extract_municipio_nome(municipio: Any) -> str | None:
    """
    Extract municipality name from a municipio object.

    rfb_cnpj_v003 uses IBGE enrichment (``municipio.nome``).
    Falls back to ``municipio.descricao`` for compatibility with
    any legacy RFB-sourced objects (rfb_cnpj_v003 format).
    """
    if not municipio:
        return None
    if isinstance(municipio, dict):
        return municipio.get("nome") or municipio.get("descricao")
    return str(municipio)
