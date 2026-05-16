# Infraestrutura OpenSearch 3.0 - Guia de Implementação

## 📋 Sumário

1. [Visão Geral](#visão-geral)
2. [Dimensionamento para 70M+ Documentos](#dimensionamento)
3. [Criação do Cluster AWS OpenSearch](#criação-cluster)
4. [Configuração do MCP Server Nativo](#mcp-server)
5. [Criação dos Índices](#criação-indices)
6. [Checklist de Validação](#checklist)

---

## 🎯 Visão Geral

### Requisitos do Projeto

| Item            | Especificação                                   |
| --------------- | ----------------------------------------------- |
| Volume de dados | **70+ milhões de documentos**                   |
| Tipos de índice | Jazidas ANM, Empresas CNPJ, Municípios IBGE     |
| Busca           | Híbrida (full-text + k-NN vetorial + geo_shape) |
| Embeddings      | 1536 dimensões (OpenAI text-embedding-3-small)  |
| Geometrias      | Polígonos complexos (geo_shape)                 |
| Integrações     | MCP Server nativo para agentes IA               |

### Distribuição Estimada de Documentos

| Índice       | Volume Estimado | Tamanho médio/doc           | Storage estimado |
| ------------ | --------------- | --------------------------- | ---------------- |
| `jazidas`    | ~500.000        | 15 KB (com embedding + geo) | ~7.5 GB          |
| `empresas`   | ~55.000.000     | 8 KB (com embedding)        | ~440 GB          |
| `municipios` | ~5.600          | 20 KB (com geo_shape)       | ~112 MB          |
| `cnaes`      | ~1.400          | 2 KB                        | ~3 MB            |
| **Total**    | **~55.500.000** | -                           | **~450 GB**      |

> ⚠️ Se considerar 70M+ com histórico/versões, dimensionar para **600 GB úteis**.

---

## 📐 Dimensionamento

### Configuração Recomendada (Produção)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                    CLUSTER OPENSEARCH 3.0 - PRODUÇÃO                                    │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                           DATA NODES (3x)                                        │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                  │   │
│  │  │ r6g.2xlarge     │  │ r6g.2xlarge     │  │ r6g.2xlarge     │                  │   │
│  │  │ 8 vCPU          │  │ 8 vCPU          │  │ 8 vCPU          │                  │   │
│  │  │ 64 GB RAM       │  │ 64 GB RAM       │  │ 64 GB RAM       │                  │   │
│  │  │ 500 GB EBS gp3  │  │ 500 GB EBS gp3  │  │ 500 GB EBS gp3  │                  │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘                  │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                        DEDICATED MASTER NODES (3x)                               │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                  │   │
│  │  │ m6g.large       │  │ m6g.large       │  │ m6g.large       │                  │   │
│  │  │ 2 vCPU          │  │ 2 vCPU          │  │ 2 vCPU          │                  │   │
│  │  │ 8 GB RAM        │  │ 8 GB RAM        │  │ 8 GB RAM        │                  │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘                  │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  Configurações:                                                                         │
│  • Multi-AZ: Habilitado (3 AZs)                                                        │
│  • Versão: OpenSearch 3.0                                                              │
│  • Encryption at rest: Habilitado                                                      │
│  • Node-to-node encryption: Habilitado                                                 │
│  • Fine-grained access control: Habilitado                                             │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

### Justificativa do Dimensionamento

| Recurso          | Cálculo                               | Valor                   |
| ---------------- | ------------------------------------- | ----------------------- |
| **Storage**      | 600 GB × 1.5 (overhead) × 1 (réplica) | 900 GB → 500 GB × 3 nós |
| **RAM**          | k-NN requer ~50% heap para vetores    | 64 GB/nó (32 GB heap)   |
| **vCPU**         | Indexação paralela + buscas híbridas  | 8 vCPU/nó               |
| **Master nodes** | Cluster > 10 shards requer dedicated  | 3 × m6g.large           |

### Configuração de Shards

| Índice       | Shards Primários | Réplicas | Total Shards | Justificativa                         |
| ------------ | ---------------- | -------- | ------------ | ------------------------------------- |
| `empresas`   | 12               | 1        | 24           | ~4.5M docs/shard (ideal < 50GB/shard) |
| `jazidas`    | 3                | 1        | 6            | Volume menor, busca geo intensiva     |
| `municipios` | 1                | 1        | 2            | Volume pequeno                        |
| `cnaes`      | 1                | 1        | 2            | Volume mínimo                         |

---

## 🚀 Criação do Cluster AWS OpenSearch

### Passo 1: Criar Domínio via Console AWS

```
AWS Console → OpenSearch Service → Create domain
```

### Passo 2: Configurações do Domínio

#### 2.1 Domain name and version

| Campo                  | Valor              |
| ---------------------- | ------------------ |
| Domain name            | `supplyradar-prod` |
| Domain creation method | Standard create    |
| Templates              | Production         |
| Engine version         | **OpenSearch 3.0** |

#### 2.2 Data nodes

| Campo                     | Valor                |
| ------------------------- | -------------------- |
| Instance type             | `r6g.2xlarge.search` |
| Number of nodes           | `3`                  |
| Storage type              | EBS (gp3)            |
| EBS storage size per node | `500` GiB            |
| IOPS                      | `3000`               |
| Throughput                | `125` MiB/s          |

#### 2.3 Dedicated master nodes

| Campo                         | Valor              |
| ----------------------------- | ------------------ |
| Enable dedicated master nodes | ✅ Yes             |
| Instance type                 | `m6g.large.search` |
| Number of master nodes        | `3`                |

#### 2.4 Network

| Campo           | Valor                           |
| --------------- | ------------------------------- |
| Network         | VPC access                      |
| VPC             | `vpc-supplyradar-prod`          |
| Subnets         | Selecionar 3 subnets (multi-AZ) |
| Security groups | `sg-opensearch-cluster`         |

#### 2.5 Fine-grained access control

| Campo                              | Valor                                             |
| ---------------------------------- | ------------------------------------------------- |
| Enable fine-grained access control | ✅ Yes                                            |
| Create master user                 | ✅ Yes                                            |
| Master username                    | `admin`                                           |
| Master password                    | (Gerar senha forte, armazenar no Secrets Manager) |

#### 2.6 Access policy

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::ACCOUNT_ID:role/MineralRadarBackendRole"
            },
            "Action": "es:*",
            "Resource": "arn:aws:es:us-east-1:ACCOUNT_ID:domain/supplyradar-prod/*"
        }
    ]
}
```

#### 2.7 Encryption

| Campo                   | Valor           |
| ----------------------- | --------------- |
| Encryption at rest      | ✅ Enable       |
| KMS key                 | AWS managed key |
| Node-to-node encryption | ✅ Enable       |

### Passo 3: Criar via Terraform (Alternativa)

```hcl
# terraform/opensearch.tf

resource "aws_opensearch_domain" "supplyradar" {
  domain_name    = "supplyradar-prod"
  engine_version = "OpenSearch_3.0"

  cluster_config {
    instance_type            = "r6g.2xlarge.search"
    instance_count           = 3
    zone_awareness_enabled   = true

    zone_awareness_config {
      availability_zone_count = 3
    }

    dedicated_master_enabled = true
    dedicated_master_type    = "m6g.large.search"
    dedicated_master_count   = 3
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = 500
    iops        = 3000
    throughput  = 125
  }

  vpc_options {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.opensearch.id]
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-PFS-2023-10"
  }

  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = true
    master_user_options {
      master_user_name     = "admin"
      master_user_password = var.opensearch_master_password
    }
  }

  tags = {
    Environment = "production"
    Project     = "MineralRadar"
  }
}

output "opensearch_endpoint" {
  value = aws_opensearch_domain.supplyradar.endpoint
}

output "opensearch_dashboard" {
  value = aws_opensearch_domain.supplyradar.dashboard_endpoint
}
```

---

## 🤖 Configuração do MCP Server Nativo

### O que é o MCP Server no OpenSearch 3.0?

O OpenSearch 3.0 introduz suporte nativo ao **Model Context Protocol (MCP)**, permitindo que agentes de IA consumam capacidades de busca diretamente via protocolo padronizado.

> **Nota AWS**: No AWS OpenSearch Service 3.x, o MCP está **integrado ao plugin ML Commons** (`opensearch-ml`). As configurações ficam sob `plugins.ml_commons.mcp_*` ao invés de `plugins.mcp.*`. Isso inclui tools como `McpSseTool` e `McpStreamableHttpTool` que são listadas via endpoint `/_plugins/_ml/tools`.

### Passo 1: Habilitar o Plugin MCP

Após criação do cluster, acessar OpenSearch Dashboards:

```
https://<domain-endpoint>/_dashboards
```

#### Via API (após cluster ativo):

```bash
# Verificar plugins disponíveis
curl -XGET "https://<endpoint>/_cat/plugins?v" \
  -u admin:PASSWORD

# Verificar status atual do MCP
curl -XGET "https://<endpoint>/_cluster/settings?include_defaults&filter_path=**.mcp**" \
  -u admin:PASSWORD

# Habilitar MCP Server (integrado ao ML Commons no AWS OpenSearch 3.x)
curl -XPUT "https://<endpoint>/_cluster/settings" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "persistent": {
      "plugins.ml_commons.mcp_server_enabled": true,
      "plugins.ml_commons.mcp_connector_enabled": true
    }
  }'

# Confirmar habilitação
curl -XGET "https://<endpoint>/_cluster/settings?pretty" \
  -u admin:PASSWORD
```

> **Nota**: No AWS OpenSearch Service 3.x, o MCP está integrado ao plugin **ML Commons** (`opensearch-ml`), não como plugin separado. As configurações de porta/host são gerenciadas automaticamente pela AWS.

### Passo 2: Configurar Tools MCP

Criar configuração de tools expostas via MCP:

```bash
curl -XPUT "https://<endpoint>/_plugins/_mcp/tools" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "tools": [
      {
        "name": "search_jazidas",
        "description": "Busca jazidas minerais por substância, localização e outros critérios",
        "index": "jazidas",
        "parameters": {
          "query_text": { "type": "string", "description": "Texto de busca" },
          "substancia": { "type": "string", "description": "Tipo de substância mineral" },
          "location": { "type": "geo_point", "description": "Coordenadas lat,lon" },
          "radius_km": { "type": "number", "description": "Raio de busca em km" }
        },
        "capabilities": ["text_search", "vector_search", "geo_search"]
      },
      {
        "name": "search_empresas",
        "description": "Busca empresas por CNAE, localização e atividade",
        "index": "empresas",
        "parameters": {
          "query_text": { "type": "string", "description": "Texto de busca" },
          "cnae": { "type": "string", "description": "Código CNAE" },
          "municipio": { "type": "string", "description": "Nome do município" },
          "uf": { "type": "string", "description": "Sigla do estado" }
        },
        "capabilities": ["text_search", "vector_search"]
      },
      {
        "name": "search_municipios",
        "description": "Busca municípios por nome ou região",
        "index": "municipios",
        "parameters": {
          "nome": { "type": "string", "description": "Nome do município" },
          "uf": { "type": "string", "description": "Sigla do estado" },
          "geometry": { "type": "geo_shape", "description": "Geometria para interseção" }
        },
        "capabilities": ["text_search", "geo_search"]
      }
    ]
  }'
```

### Passo 3: Testar Conexão MCP

```bash
# Listar tools disponíveis
curl -XGET "https://<endpoint>/_plugins/_mcp/tools" \
  -u admin:PASSWORD

# Testar chamada de tool
curl -XPOST "https://<endpoint>/_plugins/_mcp/call" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "search_jazidas",
    "arguments": {
      "query_text": "areia para construção",
      "radius_km": 50,
      "location": {
        "lat": -23.55,
        "lon": -46.63
      }
    }
  }'
```

### Passo 4: Configurar Acesso para Aplicação

Criar usuário específico para aplicação:

```bash
curl -XPUT "https://<endpoint>/_plugins/_security/api/internalusers/supplyradar_app" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "password": "APP_PASSWORD_AQUI",
    "backend_roles": ["readall", "mcp_client"],
    "attributes": {
      "application": "supplyradar"
    }
  }'

# Criar role para MCP
curl -XPUT "https://<endpoint>/_plugins/_security/api/roles/mcp_client" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "cluster_permissions": ["cluster:admin/mcp/*"],
    "index_permissions": [
      {
        "index_patterns": ["jazidas", "empresas", "municipios", "cnaes"],
        "allowed_actions": ["read", "search"]
      }
    ]
  }'

# Mapear role ao usuário
curl -XPUT "https://<endpoint>/_plugins/_security/api/rolesmapping/mcp_client" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "users": ["supplyradar_app"]
  }'
```

---

## 📊 Criação dos Índices

### Passo 1: Configurações Globais do Cluster

Antes de criar índices, ajustar configurações do cluster para k-NN:

```bash
curl -XPUT "https://<endpoint>/_cluster/settings" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "persistent": {
      "knn.memory.circuit_breaker.limit": "50%",
      "knn.memory.circuit_breaker.enabled": true,
      "knn.algo_param.index_thread_qty": 4
    }
  }'
```

### Passo 2: Criar Índice `jazidas`

```bash
curl -XPUT "https://<endpoint>/jazidas" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
  "settings": {
    "index": {
      "number_of_shards": 3,
      "number_of_replicas": 1,
      "knn": true,
      "knn.algo_param.ef_search": 100,
      "knn.algo_param.ef_construction": 256,
      "knn.algo_param.m": 16,
      "analysis": {
        "analyzer": {
          "brazilian_analyzer": {
            "type": "custom",
            "tokenizer": "standard",
            "filter": ["lowercase", "brazilian_stemmer", "asciifolding"]
          }
        },
        "filter": {
          "brazilian_stemmer": {
            "type": "stemmer",
            "language": "brazilian"
          }
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "numero_processo": { "type": "keyword" },
      "ano": { "type": "integer" },
      "area_ha": { "type": "float" },
      "fase": { "type": "keyword" },
      "ultimo_evento": { "type": "keyword" },
      "nome": { "type": "text", "analyzer": "brazilian_analyzer" },
      "subs": { "type": "keyword" },
      "uso": { "type": "keyword" },
      "uf": { "type": "keyword" },
      "municipios": { "type": "keyword" },
      "dsc_subs": {
        "type": "text",
        "analyzer": "brazilian_analyzer",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      },
      "dsc_uso": {
        "type": "text",
        "analyzer": "brazilian_analyzer",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      },
      "titular": {
        "type": "text",
        "analyzer": "brazilian_analyzer",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      },
      "cnpj_cpf_titular": { "type": "keyword" },
      "descricao_completa": {
        "type": "text",
        "analyzer": "brazilian_analyzer"
      },
      "embedding": {
        "type": "knn_vector",
        "dimension": 1536,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "nmslib",
          "parameters": {
            "ef_construction": 256,
            "m": 16
          }
        }
      },
      "location": { "type": "geo_point" },
      "geometry": { "type": "geo_shape" },
      "data_atualizacao": { "type": "date" },
      "created_at": { "type": "date" },
      "updated_at": { "type": "date" }
    }
  }
}'
```

### Passo 3: Criar Índice `empresas`

```bash
curl -XPUT "https://<endpoint>/empresas" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
  "settings": {
    "index": {
      "number_of_shards": 12,
      "number_of_replicas": 1,
      "knn": true,
      "knn.algo_param.ef_search": 100,
      "refresh_interval": "30s",
      "analysis": {
        "analyzer": {
          "brazilian_analyzer": {
            "type": "custom",
            "tokenizer": "standard",
            "filter": ["lowercase", "brazilian_stemmer", "asciifolding"]
          }
        },
        "filter": {
          "brazilian_stemmer": {
            "type": "stemmer",
            "language": "brazilian"
          }
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "cnpj": { "type": "keyword" },
      "cnpj_basico": { "type": "keyword" },
      "razao_social": {
        "type": "text",
        "analyzer": "brazilian_analyzer",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      },
      "nome_fantasia": {
        "type": "text",
        "analyzer": "brazilian_analyzer"
      },
      "situacao_cadastral": { "type": "keyword" },
      "cnae_principal": { "type": "keyword" },
      "cnae_principal_descricao": {
        "type": "text",
        "analyzer": "brazilian_analyzer"
      },
      "cnaes_secundarios": { "type": "keyword" },
      "uf": { "type": "keyword" },
      "municipio": { "type": "keyword" },
      "cep": { "type": "keyword" },
      "logradouro": { "type": "text" },
      "numero": { "type": "keyword" },
      "bairro": { "type": "text" },
      "telefone": { "type": "keyword" },
      "email": { "type": "keyword" },
      "porte": { "type": "keyword" },
      "natureza_juridica": { "type": "keyword" },
      "capital_social": { "type": "float" },
      "data_abertura": { "type": "date" },
      "embedding": {
        "type": "knn_vector",
        "dimension": 1536,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "nmslib",
          "parameters": {
            "ef_construction": 256,
            "m": 16
          }
        }
      },
      "location": { "type": "geo_point" },
      "created_at": { "type": "date" },
      "updated_at": { "type": "date" }
    }
  }
}'
```

### Passo 4: Criar Índice `municipios`

```bash
curl -XPUT "https://<endpoint>/municipios" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
  "settings": {
    "index": {
      "number_of_shards": 1,
      "number_of_replicas": 1,
      "knn": true
    }
  },
  "mappings": {
    "properties": {
      "codigo_ibge": { "type": "keyword" },
      "nome": {
        "type": "text",
        "analyzer": "brazilian",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      },
      "uf": { "type": "keyword" },
      "regiao": { "type": "keyword" },
      "mesorregiao": { "type": "keyword" },
      "microrregiao": { "type": "keyword" },
      "populacao": { "type": "integer" },
      "area_km2": { "type": "float" },
      "pib_per_capita": { "type": "float" },
      "embedding": {
        "type": "knn_vector",
        "dimension": 1536,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "nmslib"
        }
      },
      "centroid": { "type": "geo_point" },
      "geometry": { "type": "geo_shape" },
      "created_at": { "type": "date" }
    }
  }
}'
```

### Passo 5: Criar Índice `cnaes`

```bash
curl -XPUT "https://<endpoint>/cnaes" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
  "settings": {
    "index": {
      "number_of_shards": 1,
      "number_of_replicas": 1,
      "knn": true
    }
  },
  "mappings": {
    "properties": {
      "codigo": { "type": "keyword" },
      "descricao": {
        "type": "text",
        "analyzer": "brazilian",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      },
      "secao": { "type": "keyword" },
      "divisao": { "type": "keyword" },
      "grupo": { "type": "keyword" },
      "classe": { "type": "keyword" },
      "embedding": {
        "type": "knn_vector",
        "dimension": 1536,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "nmslib"
        }
      },
      "created_at": { "type": "date" }
    }
  }
}'
```

---

## ✅ Checklist de Validação

### Após Criação do Cluster

```bash
# 1. Verificar saúde do cluster
curl -XGET "https://<endpoint>/_cluster/health?pretty" -u admin:PASSWORD

# Esperado: status = "green"

# 2. Verificar nós
curl -XGET "https://<endpoint>/_cat/nodes?v" -u admin:PASSWORD

# Esperado: 3 data nodes + 3 master nodes

# 3. Verificar plugins
curl -XGET "https://<endpoint>/_cat/plugins?v" -u admin:PASSWORD

# Esperado: opensearch-knn, opensearch-ml, opensearch-neural-search, opensearch-geospatial (entre outros)

# 4. Verificar configuração k-NN
curl -XGET "https://<endpoint>/_cluster/settings?include_defaults&filter_path=**.knn**" \
  -u admin:PASSWORD

# 5. Verificar índices criados
curl -XGET "https://<endpoint>/_cat/indices?v" -u admin:PASSWORD

# 6. Verificar mapping de um índice
curl -XGET "https://<endpoint>/jazidas/_mapping?pretty" -u admin:PASSWORD

# 7. Testar busca k-NN (após indexar dados)
curl -XPOST "https://<endpoint>/jazidas/_search" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "size": 5,
    "query": {
      "knn": {
        "embedding": {
          "vector": [0.1, 0.2, ...],
          "k": 5
        }
      }
    }
  }'

# 8. Testar busca geo_shape
curl -XPOST "https://<endpoint>/jazidas/_search" \
  -u admin:PASSWORD \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "geo_distance": {
        "distance": "50km",
        "location": { "lat": -23.55, "lon": -46.63 }
      }
    }
  }'

# 9. Verificar MCP habilitado
curl -XGET "https://<endpoint>/_cluster/settings?filter_path=**.mcp**&pretty" -u admin:PASSWORD

# 10. Listar tools ML disponíveis (inclui MCP tools)
curl -XGET "https://<endpoint>/_plugins/_ml/tools?pretty" -u admin:PASSWORD
```

### Checklist Final

| Item                       | Verificação                                         | Status |
| -------------------------- | --------------------------------------------------- | ------ |
| Cluster criado             | `_cluster/health` retorna `green`                   | ✅     |
| 3 data nodes ativos        | `_cat/nodes` mostra 3 nós data (dir)                | ✅     |
| 3 master nodes ativos      | `_cat/nodes` mostra 3 masters (mr)                  | ✅     |
| Plugin k-NN habilitado     | `_cat/plugins` inclui `opensearch-knn`              | ✅     |
| MCP Server habilitado      | `_cluster/settings` → `mcp_server_enabled: true`    | ✅     |
| MCP Connector habilitado   | `_cluster/settings` → `mcp_connector_enabled: true` | ✅     |
| Índice `jazidas` criado    | `_cat/indices` lista índice                         | ⬜     |
| Índice `empresas` criado   | `_cat/indices` lista índice                         | ⬜     |
| Índice `municipios` criado | `_cat/indices` lista índice                         | ⬜     |
| Índice `cnaes` criado      | `_cat/indices` lista índice                         | ⬜     |
| Mapping k-NN correto       | `_mapping` mostra `knn_vector`                      | ⬜     |
| Mapping geo_shape correto  | `_mapping` mostra `geo_shape`                       | ⬜     |
| Tools ML disponíveis       | `_plugins/_ml/tools` lista tools (McpSseTool, etc)  | ✅     |
| Usuário app criado         | Login funciona com credenciais                      | ⬜     |
| Conexão via VPC            | App conecta ao endpoint                             | ⬜     |

---

## 💰 Estimativa de Custos

### Configuração Produção (r6g.2xlarge x3 + m6g.large x3)

| Componente   | Especificação          | Custo/hora | Custo/mês       |
| ------------ | ---------------------- | ---------- | --------------- |
| Data nodes   | r6g.2xlarge.search × 3 | $0.578 × 3 | ~$1,250         |
| Master nodes | m6g.large.search × 3   | $0.128 × 3 | ~$280           |
| EBS Storage  | 500 GB gp3 × 3         | $0.08/GB   | ~$120           |
| **Total**    |                        |            | **~$1,650/mês** |

### Configuração Desenvolvimento (menor custo)

| Componente   | Especificação        | Custo/mês     |
| ------------ | -------------------- | ------------- |
| Data nodes   | r6g.large.search × 2 | ~$210         |
| Master nodes | Não dedicado         | $0            |
| EBS Storage  | 100 GB gp3 × 2       | ~$16          |
| **Total**    |                      | **~$230/mês** |

---

## 📞 Suporte

Em caso de dúvidas sobre esta documentação:

-   **Responsável Técnico**: [Nome do arquiteto/tech lead]
-   **Canal Slack**: #supplyradar-infra
-   **AWS Support**: Business Support (se contratado)

---

_Documento gerado em: Dezembro/2025_
_Versão: 1.0_
