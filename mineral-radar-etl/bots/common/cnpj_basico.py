"""Normalização de CNPJ básico (8 dígitos) para joins ETL / OpenSearch."""


def normalize_cnpj_basico(val: str | None) -> str | None:
    """
    Extrai raiz de CNPJ com 8 dígitos a partir de string bruta (com ou sem máscara).

    - CNPJ completo (14 dígitos): primeiros 8.
    - Já básico: preenche com zeros à esquerda se tiver 1–7 dígitos.
    """
    if val is None:
        return None
    d = "".join(c for c in str(val).strip() if c.isdigit())
    if not d:
        return None
    if len(d) >= 14:
        base = d[:8]
    elif len(d) >= 8:
        base = d[:8]
    else:
        base = d.zfill(8)
    return base if len(base) == 8 else None
