"""
Diagnóstico: verifica formato de numero_processo nos inativos do OpenSearch
vs formato DSProcesso no ProcessoPessoa.txt do microdados.
"""
import zipfile
from pathlib import Path
from opensearchpy import OpenSearch

OS_URL  = "http://localhost:9200"
OS_USER = "admin"
OS_PASS = "admin"
ZIP_CANDIDATES = [
    Path.home() / ".mineralradar" / "data" / "scm" / "microdados-scm.zip",
    Path("/tmp/mineralradar_data/scm/microdados-scm.zip"),
]
ZIP = next((p for p in ZIP_CANDIDATES if p.exists()), None)
print(f"ZIP encontrado em: {ZIP or 'NENHUM!'}\n")

client = OpenSearch(
    hosts=[OS_URL], use_ssl=False, verify_certs=False,
    http_auth=(OS_USER, OS_PASS),
)

# ── 1. Amostra de inativos no OpenSearch ─────────────────────────────────────
print("=== OPENSEARCH — 10 inativos ===")
resp = client.search(index="mr_jazidas_v001", body={
    "size": 10,
    "query": {"term": {"ativo": False}},
    "_source": ["numero_processo"],
})
os_ids = []
for h in resp["hits"]["hits"]:
    pid = h["_source"].get("numero_processo")
    eid = h["_id"]
    print(f"  _id={eid!r:30s}  numero_processo={pid!r}")
    os_ids.append(pid or eid)

# ── 2. Amostra do ProcessoPessoa.txt ─────────────────────────────────────────
print(f"\n=== ProcessoPessoa.txt — 10 entradas IDTipoRelacao=1 (zip: {ZIP}) ===")
if ZIP is None:
    print(f"  ZIP não encontrado em nenhum local: {[str(p) for p in ZIP_CANDIDATES]}")
else:
    found = 0
    with zipfile.ZipFile(ZIP) as zf:
        with zf.open("microdados-scm/ProcessoPessoa.txt") as f:
            header = f.readline().decode("latin1").strip()
            print(f"  Header: {header}")
            for line in f:
                cols = line.decode("latin1").strip().split(";")
                if len(cols) < 3:
                    continue
                dsproc, idpessoa, idtipo = cols[0], cols[1], cols[2]
                if idtipo.strip() == "1":
                    print(f"  DSProcesso={dsproc!r:30s}  IDPessoa={idpessoa}")
                    found += 1
                    if found >= 10:
                        break

# ── 3. Verifica interseção direta ────────────────────────────────────────────
def _add_dot(p: str) -> str:
    if not p or "." in p:
        return p
    parts = p.split("/")
    if len(parts) != 2:
        return p
    num, year = parts
    if len(num) > 3:
        return f"{num[:-3]}.{num[-3:]}/{year}"
    return p

print(f"\n=== VERIFICAÇÃO DE MATCH (arquivo completo) ===")
if ZIP and os_ids:
    # Converte IDs do OS para formato com ponto
    targets = {_add_dot(pid): pid for pid in os_ids}
    print(f"  Buscando no ProcessoPessoa.txt completo:")
    for dotted, original in targets.items():
        print(f"    OS: {original!r:25s} → buscando: {dotted!r}")

    found_in_file: set[str] = set()
    with zipfile.ZipFile(ZIP) as zf:
        with zf.open("microdados-scm/ProcessoPessoa.txt") as f:
            f.readline()  # skip header
            for line in f:
                cols = line.decode("latin1").strip().split(";")
                if len(cols) >= 3 and cols[2].strip() == "1":  # IDTipoRelacao=1
                    dsproc = cols[0].strip()
                    if dsproc in targets:
                        found_in_file.add(dsproc)

    print(f"\n  Resultado (IDTipoRelacao=1):")
    for dotted, original in targets.items():
        status = "✅ ENCONTRADO" if dotted in found_in_file else "❌ NÃO ENCONTRADO"
        print(f"    {original!r:25s} → {dotted!r:30s}  {status}")
