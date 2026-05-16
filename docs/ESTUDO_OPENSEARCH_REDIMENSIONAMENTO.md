# Estudo de Redimensionamento AWS OpenSearch

**Data:** 05/03/2026
**Autor:** Leonardo de Melo — Especialista em Engenharia de Software e IA
**Objetivo:** Consolidar dois clusters em um, migrar índices e reduzir custos antes do fim dos créditos AWS (31/03/2026)

---

## 1. Cenário Atual (levantado via API em 05/03/2026)

### 1.1 Clusters Ativos

| Cluster | Engine | AZs | Health | Docs | Storage Total | Storage Mín. |
|---------|--------|-----|--------|------|---------------|-------------|
| `opensearchbimcacajazidas` | OpenSearch 2.19 | 1-AZ | Yellow | 56.407.269 | 299,48 GiB | 299,48 GiB |
| `supplyradar-prod` | OpenSearch 3.3 | 3-AZ | **Green** | 482.917.406 | 845,33 GiB | 271,95 GiB |

**Custo total fev/2026:** US$ 3.051,72 (via email AWS)

### 1.2 Cluster `supplyradar-prod` — Detalhamento

**Topologia (via `_cat/nodes`):**

| Tipo | Quantidade | RAM Total | Heap Max | Disco |
|------|-----------|-----------|----------|-------|
| **Data nodes** (`dir`) | 3 | 32 GiB cada (~33 GB) | 16 GiB | 491,9 GiB cada |
| **Master nodes** (`mr`) | 3 | 8 GiB cada (~8,1 GB) | 5 GiB | 4,7 GiB cada |

**Instâncias identificadas (pela RAM):**
- Data nodes: **r7g.xlarge.search** (4 vCPU, 32 GiB RAM, EBS) — 3 unidades
- Master nodes: **m7g.large.search** (2 vCPU, 8 GiB RAM) — 3 unidades (dedicados)

> Nota: O gerente confirmou que r6g está depreciado e o cluster usa família r7g/m7g.

**Uso de disco atual (via `_cat/allocation`):**

| Nó | Shards | Disco Usado | Disco Livre | Disco Total | % Uso |
|----|--------|-------------|-------------|-------------|-------|
| Data 1 | 48 | 90,2 GiB | 401,7 GiB | 491,9 GiB | 18% |
| Data 2 | 48 | 95,4 GiB | 396,4 GiB | 491,9 GiB | 19% |
| Data 3 | 48 | 100,1 GiB | 391,7 GiB | 491,9 GiB | 20% |
| **Total** | **144** | **285,7 GiB** | **1.189,8 GiB** | **1.475,7 GiB** | **19%** |

### 1.3 Índices Ativos no `supplyradar-prod`

| Índice | Shards (pri) | Réplicas | Docs | Tamanho Primário | Tamanho Total |
|--------|-------------|----------|------|-----------------|---------------|
| `rfb_cnpj_v003` | 15 | 2 | 220.997.521 | 99,3 GiB | 268,4 GiB |
| `anm_v003` | 3 | 2 | 20.456.698 | 4,8 GiB | 14,5 GiB |
| `ibge_municipio_v001` | 1 | 2 | 5.631 | 931 MiB | 2,7 GiB |
| `rfb_cnae_v001` | 1 | 2 | 2.394 | — | 50,5 MiB |
| `anm_substancia_v001` | 1 | 2 | 862 | — | 15,3 MiB |
| `anm_tipo-uso-substancia_v001` | 1 | 2 | 26 | — | 814,9 KiB |
| + índices sistema (.plugins-*) | — | — | — | — | ~1 MiB |

**Dados primários efetivos: ~105 GiB**
**Com 2 réplicas (×3): ~285,7 GiB utilizados**

---

## 2. Migração dos Índices do Cluster BIM

### 2.1 Índices a Migrar

| Índice | Tamanho (dados primários) |
|--------|--------------------------|
| `smat_material_v002` | 19,9 GiB |
| `sap_material_v002` | 36,2 GiB |
| `sap_grupos_mercadorias_v002` | 0,3 GiB (~315 KiB) |
| `embedding_ada3_v001` | 68,8 GiB |
| **Total primário** | **~125,2 GiB** |

### 2.2 Impacto no `supplyradar-prod` Pós-Migração

| Métrica | Antes | Depois (com 2 réplicas) | Depois (com 1 réplica) |
|---------|-------|------------------------|------------------------|
| Dados primários | ~105 GiB | ~230 GiB | ~230 GiB |
| Dados totais (com réplicas) | ~285 GiB | **~690 GiB** | **~460 GiB** |
| Disco total disponível | 1.475,7 GiB | 1.475,7 GiB | 1.475,7 GiB |
| **% Uso disco** | **19%** | **47%** | **31%** |
| Espaço livre | 1.190 GiB | ~786 GiB | ~1.016 GiB |

**Conclusão:** A migração cabe confortavelmente no cluster atual em qualquer cenário de réplica. O uso de disco não ultrapassa 50% nem com 2 réplicas completas.

### 2.3 Processo de Migração (Reindex Cross-Cluster)

Como os clusters usam versões diferentes (2.19 vs 3.3), a migração deve ser feita via **snapshot/restore** ou **reindex remoto**:

```bash
# Opção 1: Reindex remoto (se remote reindex estiver habilitado)
POST _reindex
{
  "source": {
    "remote": {
      "host": "https://<endpoint-cluster-bim>:443",
      "username": "admin",
      "password": "..."
    },
    "index": "smat_material_v002"
  },
  "dest": {
    "index": "smat_material_v002"
  }
}

# Opção 2: Snapshot/Restore via S3 (recomendado)
# 1. Criar snapshot no cluster BIM → bucket S3
# 2. Registrar o mesmo bucket S3 no supplyradar-prod
# 3. Restore dos 4 índices
```

**Tempo estimado:** 2-4 horas para os 125 GiB (depende da rede entre clusters).

---

## 3. Cenários de Custo

### 3.1 Preços Unitários (sa-east-1, On-Demand)

| Recurso | Preço/hora | Preço/mês (730h) |
|---------|-----------|-----------------|
| r7g.xlarge.search (data, 4vCPU/32GiB) | US$ 0,356 | US$ 259,88 |
| r7g.large.search (data, 2vCPU/16GiB) | US$ 0,178 | US$ 129,94 |
| m7g.large.search (master, 2vCPU/8GiB) | ~US$ 0,142 | ~US$ 103,66 |
| EBS gp3 (por GiB/mês) | — | US$ 0,08 |

### 3.2 Cenário A — Atual (Sem Alteração) + Migração

**2 clusters rodando:**

| Componente | Unidades | Custo/mês |
|-----------|---------|-----------|
| **supplyradar-prod** | | |
| r7g.xlarge.search (data) | 3 | US$ 779,64 |
| m7g.large.search (master) | 3 | US$ 310,98 |
| EBS gp3 (3 × 492 GiB) | 1.476 GiB | US$ 118,08 |
| **Subtotal supplyradar-prod** | | **US$ 1.208,70** |
| **opensearchbimcacajazidas** | | **~US$ 1.843,02** * |
| **TOTAL CENÁRIO A** | | **~US$ 3.051,72** |

\* Valor inferido: US$ 3.051,72 (fatura) − US$ 1.208,70 = ~US$ 1.843,02

### 3.3 Cenário B — Cluster Único (Desligar BIM) — Manter Configuração Atual

**Após desligar `opensearchbimcacajazidas` e migrar índices:**

| Componente | Unidades | Custo/mês |
|-----------|---------|-----------|
| r7g.xlarge.search (data) | 3 | US$ 779,64 |
| m7g.large.search (master) | 3 | US$ 310,98 |
| EBS gp3 (3 × 492 GiB) | 1.476 GiB | US$ 118,08 |
| **TOTAL CENÁRIO B** | | **US$ 1.208,70** |

| | Valor |
|---|---|
| **Economia vs Cenário A** | **US$ 1.843,02/mês (−60,4%)** |

### 3.4 Cenário C — Cluster Único + Reduzir Réplicas (2 → 1)

Reduzir réplicas de 2 para 1 diminui o fator de multiplicação de ×3 para ×2:

```bash
PUT */_settings
{
  "index": {
    "number_of_replicas": 1
  }
}
```

**Impacto:**
- Dados totais: ~460 GiB (vs 690 GiB com 2 réplicas)
- Disco ocupado por nó: ~153 GiB (~31% de 492 GiB) — confortável

> **ATENÇÃO:** Com 3 data nodes em 3 AZs e 1 réplica, a perda de 1 AZ inteira ainda mantém os dados acessíveis (1 primária + 1 réplica = mínimo 2 cópias, distribuídas em 2 AZs). O cluster continua **Green**.

| Componente | Unidades | Custo/mês |
|-----------|---------|-----------|
| r7g.xlarge.search (data) | 3 | US$ 779,64 |
| m7g.large.search (master) | 3 | US$ 310,98 |
| EBS gp3 (3 × 492 GiB) | 1.476 GiB | US$ 118,08 |
| **TOTAL CENÁRIO C** | | **US$ 1.208,70** |

> Nota: Reduzir réplicas não reduz custo de instância/disco provisionado, mas libera ~230 GiB de disco para crescimento futuro e reduz carga de I/O. Abre caminho para o Cenário D.

### 3.5 Cenário D — Cluster Único + 2 Data Nodes + 1 Réplica

**Premissa:** Com 1 réplica (×2), 2 data nodes são suficientes.

> **ATENÇÃO CRÍTICA:** Em 3-AZ com 2 data nodes, o AWS OpenSearch exige **mínimo de 2 data nodes em 2 AZs**. Isso funciona, mas a perda de 1 nó temporariamente deixa o cluster **Yellow** até recuperação.

| Componente | Unidades | Custo/mês |
|-----------|---------|-----------|
| r7g.xlarge.search (data) | **2** | US$ 519,76 |
| m7g.large.search (master) | 3 | US$ 310,98 |
| EBS gp3 (2 × 492 GiB) | 984 GiB | US$ 78,72 |
| **TOTAL CENÁRIO D** | | **US$ 909,46** |

| | Valor |
|---|---|
| **Economia vs Cenário A** | **US$ 2.142,26/mês (−70,2%)** |
| **Economia vs Cenário B** | **US$ 299,24/mês (−24,8%)** |

**Disco por nó:** ~460 GiB ÷ 2 = ~230 GiB por nó (46% de 492 GiB) — ainda confortável.

### 3.6 Cenário E — Cluster Único + 1 Data Node (Mínimo Absoluto)

**Premissa (conforme sugestão do gerente):** Reduzir para 1 data node. Sem réplicas (0).

> **RISCO ALTO:** Sem réplicas, a perda do nó = **perda total de dados**. Requer backup regular.
> AWS não permite 1-AZ em clusters 3-AZ existentes — seria necessário recriar o cluster como **1-AZ**.

```bash
PUT */_settings
{
  "index": {
    "number_of_replicas": 0
  }
}
```

| Componente | Unidades | Custo/mês |
|-----------|---------|-----------|
| r7g.xlarge.search (data) | **1** | US$ 259,88 |
| Master dedicado (removível em 1-AZ) | **0** | US$ 0,00 |
| EBS gp3 (1 × 492 GiB) | 492 GiB | US$ 39,36 |
| **TOTAL CENÁRIO E** | | **US$ 299,24** |

| | Valor |
|---|---|
| **Economia vs Cenário A** | **US$ 2.752,48/mês (−90,2%)** |

**Disco no único nó:** ~230 GiB (46% de 492 GiB) — cabe com folga.

### 3.7 Cenário F — Cluster Único + Downscale de Instância (r7g.large)

**Premissa:** Trocar `r7g.xlarge` (32 GiB) por `r7g.large` (16 GiB) — metade da RAM e vCPUs.

| Config | Data Nodes | Réplicas | Instância | Custo/mês |
|--------|-----------|----------|-----------|-----------|
| F1: 2 nodes + 1 réplica | 2 | 1 | r7g.large | US$ 260 + US$ 311 + US$ 79 = **~US$ 650** |
| F2: 1 node + 0 réplicas (1-AZ, sem master) | 1 | 0 | r7g.large | US$ 130 + US$ 39 = **~US$ 169** |

> **Risco F2:** Mínimo absoluto. ~230 GiB de dados primários em nó com 16 GiB RAM. O OpenSearch recomenda ~1 GiB heap por 30 GiB de dados. Com 8 GiB heap para 230 GiB, está no limite (ideal: 7,7 GiB).

---

## 4. Quadro Comparativo

| Cenário | Data | Master | Réplicas | AZs | Custo/Mês | Economia | Risco |
|---------|------|--------|----------|-----|-----------|----------|-------|
| **A** Atual (2 clusters) | 3+? | 3+? | 2 | 3+1 | US$ 3.052 | — | Nenhum |
| **B** Cluster único | 3 | 3 | 2 | 3 | US$ 1.209 | −60% | Baixo |
| **C** B + réplicas 2→1 | 3 | 3 | 1 | 3 | US$ 1.209 | −60% | Baixo |
| **D** 2 data + 1 réplica | 2 | 3 | 1 | 3 | US$ 909 | −70% | Médio |
| **E** 1 data + 0 réplica (1-AZ) | 1 | 0 | 0 | 1 | US$ 299 | −90% | **Alto** |
| **F1** 2× r7g.large + 1 réplica | 2 | 3 | 1 | 3 | US$ 650 | −79% | Médio |
| **F2** 1× r7g.large, sem réplica | 1 | 0 | 0 | 1 | US$ 169 | −94% | **Muito Alto** |

---

## 5. Impactos e Riscos

### 5.1 Redução de Réplicas (2 → 1)

| Aspecto | Impacto |
|---------|---------|
| Disponibilidade | Tolerância a falha de 1 AZ mantida (2 cópias em 2 AZs) |
| Performance de leitura | Leve redução (~33% menos shards para distribuir queries) |
| Recovery time | Mais rápido (menos dados para replicar ao adicionar nó) |
| **Recomendação** | **Seguro para ambiente de produção com 3 AZs** |

### 5.2 Redução de Data Nodes (3 → 2)

| Aspecto | Impacto |
|---------|---------|
| Disponibilidade | Perda de 1 nó = cluster **Yellow** temporário |
| Performance | ~33% menos throughput de busca paralela |
| Rebalanceamento | Shards redistribuídos em 2 nós (mais denso) |
| **Recomendação** | **Aceitável para carga atual (baixa utilização de CPU: 0-18%)** |

### 5.3 Redução para 1 Data Node (3 → 1)

| Aspecto | Impacto |
|---------|---------|
| Disponibilidade | **Ponto único de falha — qualquer queda = downtime total** |
| Réplicas | Obrigatoriamente 0 (não há outro nó) |
| Recovery | Requer restore de snapshot S3 (~1-2 horas) |
| **Recomendação** | **Somente aceitável com snapshot automático diário + tolerância a downtime** |

### 5.4 Migração dos Índices do BIM

| Aspecto | Impacto |
|---------|---------|
| Compatibilidade | OpenSearch 2.19 → 3.3 (compatível via snapshot/restore) |
| Tempo | ~2-4 horas via S3 snapshot |
| Risco | Nenhum (operação aditiva, não destrutiva) |
| Embeddings (68,8 GiB) | Índice k-NN grande — pode aumentar heap usage (~2,3 GiB extra de heap) |

---

## 6. Recomendação

### Curto prazo (abril/2026) — Cenário B+C

1. **Migrar os 4 índices** do BIM para `supplyradar-prod` via snapshot S3
2. **Desligar cluster `opensearchbimcacajazidas`** → economia imediata de ~US$ 1.843/mês
3. **Reduzir réplicas de 2 para 1** → libera ~230 GiB para crescimento

**Custo: ~US$ 1.209/mês** (economia de 60%)

### Médio prazo (maio-junho/2026) — Cenário D

4. Após estabilização, **reduzir de 3 para 2 data nodes**
5. Monitorar CPU e heap por 2 semanas

**Custo: ~US$ 909/mês** (economia de 70%)

### Longo prazo (avaliação) — Cenário E ou F1

6. Se a carga continuar baixa e houver tolerância a risco, avaliar 1 nó ou downscale de instância

---

## 7. Comandos Utilizados

```bash
# Nós do cluster (tipo, RAM, disco)
curl -u admin:*** "https://<endpoint>/_cat/nodes?v&h=name,ip,heap.percent,ram.percent,cpu,disk.total,disk.used,disk.avail,node.role"

# Saúde do cluster
curl -u admin:*** "https://<endpoint>/_cluster/health?pretty"

# Índices (tamanho, shards, réplicas)
curl -u admin:*** "https://<endpoint>/_cat/indices?v&h=index,health,status,pri,rep,docs.count,store.size&s=store.size:desc"

# Alocação de disco por nó
curl -u admin:*** "https://<endpoint>/_cat/allocation?v&h=shards,disk.indices,disk.used,disk.avail,disk.total,disk.percent,node"

# Detalhes de índices principais
curl -u admin:*** "https://<endpoint>/_cat/indices/rfb_cnpj_v003,anm_v003?v&h=index,pri,rep,docs.count,pri.store.size,store.size"

# RAM e Heap por nó
curl -u admin:*** "https://<endpoint>/_nodes/stats/os,jvm?filter_path=nodes.*.os.mem.total_in_bytes,nodes.*.name,nodes.*.jvm.mem.heap_max_in_bytes"

# Configurações do cluster
curl -u admin:*** "https://<endpoint>/_cluster/settings?include_defaults=true&flat_settings=true&pretty"

# Alterar réplicas
PUT */_settings {"index": {"number_of_replicas": 1}}
```

---

## 8. Próximos Passos

- [ ] Confirmar tipo exato das instâncias via console AWS (EC2 > OpenSearch domain details)
- [ ] Criar snapshot S3 dos 4 índices no cluster BIM
- [ ] Restaurar snapshot no `supplyradar-prod`
- [ ] Validar índices migrados (contagem de docs, queries de teste)
- [ ] Desligar cluster `opensearchbimcacajazidas`
- [ ] Alterar réplicas de 2 para 1 em todos os índices
- [ ] Monitorar por 2 semanas antes de reduzir data nodes
