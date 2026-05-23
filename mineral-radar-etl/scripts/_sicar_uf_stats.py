"""Contagem CAR por UF no OpenSearch (validação ingestão). Uso: python scripts/_sicar_uf_stats.py"""
from opensearchpy import OpenSearch

# Referência aproximada WFS (bot_sicar.py, mai/2026)
WFS_REF = {
    "MG": 1_200_000, "SP": 900_000, "PA": 820_000, "BA": 700_000, "MT": 600_000,
    "GO": 550_000, "RS": 500_000, "PR": 450_000, "RO": 200_000, "TO": 200_000,
    "MA": 200_000, "MS": 200_000, "AM": 150_000, "CE": 150_000, "PI": 100_000,
    "SC": 200_000, "PE": 150_000, "RN": 80_000, "ES": 80_000, "AL": 60_000,
    "PB": 60_000, "SE": 50_000, "RR": 30_000, "AC": 56_231, "AP": 30_000,
    "RJ": 50_000, "DF": 10_000,
}
ALL_UFS = [
    "MG", "SP", "PA", "BA", "MT", "GO", "RS", "PR", "RO", "TO",
    "MA", "MS", "AM", "CE", "PI", "SC", "PE", "RN", "ES", "AL",
    "PB", "SE", "RR", "AC", "AP", "RJ", "DF",
]

def main() -> None:
    c = OpenSearch(["http://localhost:9200"], verify_certs=False)
    total = c.count(index="mr_sicar_v001")["count"]
    body = {
        "size": 0,
        "aggs": {"por_uf": {"terms": {"field": "uf.keyword", "size": 30}}},
    }
    buckets = c.search(index="mr_sicar_v001", body=body)["aggregations"]["por_uf"]["buckets"]

    indexed = {b["key"]: b["doc_count"] for b in buckets}
    ref_sum = sum(WFS_REF.values())

    print(f"Total no índice: {total:,}")
    print(f"Soma ref. UFs (estimativa): {ref_sum:,} (~6,8M citado no projeto)")
    print(f"Cobertura vs ref.: {100 * total / ref_sum:.1f}%")
    print(f"UFs com ≥1 doc: {len(indexed)}/27\n")
    print(f"{'UF':<4} {'Índice':>10} {'Ref.WFS':>10} {'%':>7}  Status")
    print("-" * 48)
    for uf in ALL_UFS:
        n = indexed.get(uf, 0)
        ref = WFS_REF.get(uf, 100_000)
        pct = 100 * n / ref if ref else 0
        if n == 0:
            st = "AUSENTE"
        elif pct >= 90:
            st = "OK"
        elif pct >= 50:
            st = "PARCIAL"
        else:
            st = "INCOMPLETO"
        print(f"{uf:<4} {n:>10,} {ref:>10,} {pct:>6.1f}%  {st}")

    missing = [u for u in ALL_UFS if indexed.get(u, 0) == 0]
    if missing:
        print(f"\nUFs sem documentos: {', '.join(missing)}")

if __name__ == "__main__":
    main()
