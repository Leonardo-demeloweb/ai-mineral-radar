#!/usr/bin/env python3
"""
=================================================================
  REINDEX: anm_v001 → anm_v002 (com flatten de detalhesCNPJ)
=================================================================

Script para reindexar ~956K documentos do anm_v001 para anm_v002,
removendo o bloco `detalhesCNPJ` de `pessoas` (50+ campos redundantes
que já existem no índice cnpj_v001) e aplicando transformações.

Etapas:
  1. Deletar anm_v002 existente (está vazio)
  2. Criar anm_v002 com mapping otimizado
  3. Scroll/Scan no anm_v001 (lotes de 1000)
  4. Transform + Bulk index no anm_v002 (lotes de 500)

Uso:
  python scripts/reindex_anm_v002.py

  # Modo dry-run (não escreve, só valida):
  python scripts/reindex_anm_v002.py --dry-run

  # Limitar documentos (para teste):
  python scripts/reindex_anm_v002.py --limit 1000

Requisitos:
  pip install opensearch-py
  
Tempo estimado: ~30-60 min para 956K docs (depende da rede)

Autor: Leonardo Melo
Data: 2026-02-10
=================================================================
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from opensearchpy import OpenSearch, helpers

# =====================================================
# CONFIGURAÇÃO
# =====================================================

OPENSEARCH_ENDPOINT = "https://search-supplyradar-prod-5arrhz7f5fcgh2xpj6uevjigoa.aos.sa-east-1.on.aws"
OPENSEARCH_USER = "admin"
OPENSEARCH_PASSWORD = "Lm26mqW53fY?"

SOURCE_INDEX = "anm_v001"
TARGET_INDEX = "anm_v002"

SCROLL_SIZE = 1000       # Docs por scroll
BULK_SIZE = 500          # Docs por bulk request
SCROLL_TIMEOUT = "5m"    # Tempo de vida do scroll

# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"reindex_anm_v002_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
    ],
)
logger = logging.getLogger("reindex")

# =====================================================
# MAPPING DO anm_v002 (OTIMIZADO)
# =====================================================
# 
# Mudanças em relação ao mapping v001:
#   1. pessoas: REMOVIDO detalhesCNPJ (50+ campos → cnpjBasico como ID de ligação)
#   2. municipios: MANTIDO nested 
#   3. shapes: MANTIDO nested com geo_shape 
#   4. substancias: nested LEVE (5 campos: id, tipoUso, motivo, dtInicio, dtFim)
#      + idSubstancias[] + nmSubstancias[] flat no root (QPT nativo)
#   4b. tipoUsoSubstancia: idTipoUsoSubstancias[] + dsTipoUsoSubstancias[] flat no root
#      (extraído de shapes, permite QPT filtrar por tipo de uso sem nested query)
#   5. localizacao: geo_point no ROOT
#   6. siglasUF, nomesMunicipios: extraídos de municipios[] para root (QPT nativo)
#   7. nmTitulares, cnpjTitulares: extraídos de pessoas[] (relação=Titular) para root (QPT nativo)
#   8. Casing normalizado: Substancia→substancia, TipoDocumentoLegal→tipoDocumentoLegal
#   9. Campos flat renomeados para plural (são arrays): siglasUF, nomesMunicipios, etc.
#
# O que NÃO muda: eventos, titulos, documentacoes, associacoes
# =====================================================

ANM_V002_SETTINGS = {
    "settings": {
        "number_of_shards": 3,
        "number_of_replicas": 2,
        "analysis": {
            "normalizer": {
                "lower_ascii": {
                    "type": "custom",
                    "filter": ["lowercase", "asciifolding"]
                }
            },
            "analyzer": {
                "pt_brazilian": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding", "brazilian_stemmer"]
                }
            },
            "filter": {
                "brazilian_stemmer": {
                    "type": "stemmer",
                    "language": "brazilian"
                }
            }
        }
    },
    "mappings": {
        "properties": {
            # ======== CAMPOS ROOT (flat) ========
            "dsProcesso": {
                "type": "keyword",
                "normalizer": "lower_ascii"
            },
            "nrProcesso": {"type": "integer"},
            "nrAnoProcesso": {"type": "integer"},
            "nrNUP": {
                "type": "keyword",
                "normalizer": "lower_ascii"
            },
            "btAtivo": {
                "type": "keyword",
                "normalizer": "lower_ascii"
            },
            "qtAreaHa": {"type": "double"},
            "dtProtocolo": {
                "type": "date",
                "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
            },
            "dtPrioridade": {
                "type": "date",
                "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
            },

            # ======== HASH (controle ETL — comparação com SQL) ========
            "hash": {"type": "keyword"},

            # ======== GEO NO ROOT (flatten do primeiro polígono) ========
            "localizacao": {"type": "geo_point"},

            # ======== FASE / REQUERIMENTO (object, não nested) ========
            "faseProcesso": {
                "properties": {
                    "idFaseProcesso": {"type": "integer"},
                    "dsFaseProcesso": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    }
                }
            },
            "tipoRequerimento": {
                "properties": {
                    "idTipoRequerimento": {"type": "integer"},
                    "dsTipoRequerimento": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    }
                }
            },
            "unidadeAdministrativaRegional": {
                "properties": {
                    "idUnidadeAdministrativaRegional": {"type": "integer"},
                    "dsUnidadeAdministrativaRegional": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    }
                }
            },
            "unidadeProtocolizadora": {
                "properties": {
                    "idUnidadeProtocolizadora": {"type": "integer"},
                    "dsUnidadeProtocolizadora": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    }
                }
            },

            # ======== SHAPES (nested) - polígonos com geo_shape ========
            "shapes": {
                "type": "nested",
                "properties": {
                    "id": {"type": "keyword", "normalizer": "lower_ascii"},
                    "titular": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                    "areaHa": {"type": "double"},
                    "ativa": {"type": "boolean"},
                    "localizacao": {"type": "geo_point"},
                    "poligono": {"type": "geo_shape"},
                    "substancia": {
                        "properties": {
                            "idSubstancia": {"type": "integer"},
                            "nmSubstancia": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "tipoUsoSubstancia": {
                        "properties": {
                            "idTipoUsoSubstancia": {"type": "integer"},
                            "dsTipoUsoSubstancia": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "faseProcesso": {
                        "properties": {
                            "idFaseProcesso": {"type": "integer"},
                            "dsFaseProcesso": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    # Campos flat para busca direta (sem nested query)
                    "shapeSubstancias": {"type": "keyword", "normalizer": "lower_ascii"},
                    "shapeFaseProcesso": {"type": "keyword", "normalizer": "lower_ascii"},
                    "shapeTipoUsoSubstancia": {"type": "keyword", "normalizer": "lower_ascii"},
                }
            },

            # ======== SUBSTÂNCIAS (FLAT) — só IDs + nomes no root ========
            # Detalhes (tipo de uso, vigência, etc.) → consultar anm_substancia_v001
            "idSubstancias": {"type": "integer"},        # array simples: [45, 67]
            "nmSubstancias": {                            # array simples: ["AREIA", "ARGILA"]
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                "analyzer": "pt_brazilian"
            },

            # ======== TIPO DE USO (FLAT) — extraído de shapes[].tipoUsoSubstancia ========
            # Permite QPT filtrar por tipo de uso sem nested query
            # Ligação com anm_tipo_uso_substancia_v001 via idTipoUsoSubstancia
            "idTipoUsoSubstancias": {"type": "integer"},  # array simples: [1, 3]
            "dsTipoUsoSubstancias": {                      # array simples: ["BRITA", "INDUSTRIAL"]
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                "analyzer": "pt_brazilian"
            },

            # ======== MUNICÍPIO/UF (FLAT) — extraído de municipios[] ========
            # Processo pode ter N municípios → arrays: ["SP","RN"], ["Guarulhos","Natal"]
            "siglasUF": {"type": "keyword", "normalizer": "lower_ascii"},
            "nomesMunicipios": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                "analyzer": "pt_brazilian"
            },

            # ======== TITULARES (FLAT) — extraído de pessoas[] onde relação=Titular ========
            # Pode ter N titulares → arrays: ["EMPRESA X","EMPRESA Y"], ["12345678","87654321"]
            "nmTitulares": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                "analyzer": "pt_brazilian"
            },
            "cnpjTitulares": {"type": "keyword", "normalizer": "lower_ascii"},

            # ======== SUBSTÂNCIAS (nested) — com vigência e encerramento ========
            # IDs + nomes já estão flat no root (idSubstancias, nmSubstancias)
            # Este nested preserva a relação substância↔uso↔vigência↔encerramento
            "substancias": {
                "type": "nested",
                "properties": {
                    "substancia": {
                        "properties": {
                            "idSubstancia": {"type": "integer"},
                            "nmSubstancia": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "tipoUsoSubstancia": {
                        "properties": {
                            "idTipoUsoSubstancia": {"type": "integer"},
                            "dsTipoUsoSubstancia": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "motivoEncerramentoSubstancia": {
                        "properties": {
                            "idMotivoEncerramentoSubstancia": {"type": "integer"},
                            "dsMotivoEncerramentoSubstancia": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "dtInicioVigencia": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                    "dtFimVigencia": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                }
            },

            # ======== MUNICÍPIOS (nested) ========
            "municipios": {
                "type": "nested",
                "properties": {
                    "idMunicipio": {"type": "integer"},
                    "idMunicipio6": {"type": "integer"},
                    "idMunicipioANM": {"type": "integer"},
                    "idMunicipioRFB": {"type": "integer"},
                    "idMunicipioBCB": {"type": "integer"},
                    "idMunicipioTSE": {"type": "integer"},
                    "nome": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                    "siglaUF": {"type": "keyword", "normalizer": "lower_ascii"},
                    "nomeUF": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                    "nomeRegiao": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                    "nomeMesorregiao": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                    "nomeMicrorregiao": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                    "idMesorregiao": {"type": "integer"},
                    "idMicrorregiao": {"type": "integer"},
                    "capitalUF": {"type": "boolean"},
                    "amazoniaLegal": {"type": "boolean"},
                    "localizacao": {"type": "geo_point"},
                    "localizacaoCentroEconomico": {"type": "geo_point"},
                }
            },

            # ======== PESSOAS (nested) — SEM detalhesCNPJ ========
            #  dados básicos da pessoa + cnpjBasico como ID de ligação.
            # Para detalhes do CNPJ → consultar cnpj_v001 via cnpjBasico.
            "pessoas": {
                "type": "nested",
                "properties": {
                    "pessoa": {
                        "properties": {
                            "idPessoa": {"type": "integer"},
                            "nmPessoa": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            },
                            "nrCpfCnpj": {"type": "keyword", "normalizer": "lower_ascii"},
                            "tpPessoa": {"type": "keyword", "normalizer": "lower_ascii"},
                        }
                    },
                    # ID de ligação com cnpj_v001 (extraído de detalhesCNPJ.empresa.cnpjBasico)
                    "cnpjBasico": {"type": "keyword", "normalizer": "lower_ascii"},
                    "tipoRelacao": {
                        "properties": {
                            "idTipoRelacao": {"type": "integer"},
                            "dsTipoRelacao": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "tipoResponsabilidadeTecnica": {
                        "properties": {
                            "idTipoResponsabilidadeTecnica": {"type": "integer"},
                            "dsTipoResponsabilidadeTecnica": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "tipoRepresentacaoLegal": {
                        "properties": {
                            "idTipoRepresentacaoLegal": {"type": "integer"},
                            "dsTipoRepresentacaoLegal": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "dtInicioVigencia": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                    "dtFimVigencia": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                    "dtPrazoArrendamento": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                }
            },

            # ======== EVENTOS (nested) ========
            "eventos": {
                "type": "nested",
                "properties": {
                    "evento": {
                        "properties": {
                            "idEvento": {"type": "integer"},
                            "dsEvento": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "dtEvento": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                    "obEvento": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                    "dsPublicacaoDOU": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                }
            },

            # ======== TÍTULOS (nested) ========
            "titulos": {
                "type": "nested",
                "properties": {
                    "nrTitulo": {"type": "integer"},
                    "documentoLegal": {
                        "properties": {
                            "idDocumentoLegal": {"type": "integer"},
                            "dsDocumentoLegal": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "tipoDocumentoLegal": {
                        "properties": {
                            "idTipoDocumentoLegal": {"type": "integer"},
                            "dsTipoDocumentoLegal": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "situacaoDocumentoLegal": {
                        "properties": {
                            "idSituacaoDocumentoLegal": {"type": "integer"},
                            "dsSituacaoDocumentoLegal": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "dtPublicacao": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                    "dtVencimento": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                }
            },

            # ======== ASSOCIAÇÕES (nested) ========
            "associacoes": {
                "type": "nested",
                "properties": {
                    "dsProcessoAssociado": {"type": "keyword", "normalizer": "lower_ascii"},
                    "tipoAssociacao": {
                        "properties": {
                            "idTipoAssociacao": {"type": "integer"},
                            "dsTipoAssociacao": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "dtAssociacao": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                    "dtDesassociacao": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                    "obAssociacao": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                        "analyzer": "pt_brazilian"
                    },
                }
            },

            # ======== DOCUMENTAÇÕES (nested)  ========
            "documentacoes": {
                "type": "nested",
                "properties": {
                    "tipoDocumento": {
                        "properties": {
                            "idTipoDocumento": {"type": "integer"},
                            "dsTipoDocumento": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    },
                    "dtProtocolo": {
                        "type": "date",
                        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"
                    },
                }
            },

            # ======== PROPRIEDADE SOLO (nested) —  ========
            "propriedadeSolo": {
                "type": "nested",
                "properties": {
                    "condicaoPropriedadeSolo": {
                        "properties": {
                            "idCondicaoPropriedadeSolo": {"type": "integer"},
                            "dsCondicaoPropriedadeSolo": {
                                "type": "text",
                                "fields": {"keyword": {"type": "keyword", "normalizer": "lower_ascii"}},
                                "analyzer": "pt_brazilian"
                            }
                        }
                    }
                }
            },
        }
    }
}


# =====================================================
# TRANSFORMAÇÃO DO DOCUMENTO
# =====================================================

def transform_document(source: dict) -> dict:
    """
    Transforma um documento do anm_v001 para o formato anm_v002.
    
    Transformações:
      1. poligonos → shapes (renomeia, corrige geo_shape)
      2. pessoas: remove detalhesCNPJ, extrai cnpjBasico como ID de ligação
      3. localizacao: extrai do primeiro polígono para o root
      4. documentacao → documentacoes (renomeia)
      5. eventos: corrige casing (Evento → evento)
    """
    doc = {}
    
    # ---- 1. Campos root (direto) ----
    for field in [
        "dsProcesso", "nrProcesso", "nrAnoProcesso", "nrNUP",
        "btAtivo", "qtAreaHa", "dtProtocolo", "dtPrioridade",
        "faseProcesso", "tipoRequerimento",
        "unidadeAdministrativaRegional", "unidadeProtocolizadora",
        "hash",  # controle ETL — comparação com SQL
    ]:
        if field in source:
            doc[field] = source[field]
    
    # ---- 2. Shapes (poligonos → shapes) ----
    shapes = []
    first_localizacao = None
    
    for pol in source.get("poligonos", []):
        shape = {
            "id": pol.get("id"),
            "titular": pol.get("titular", ""),
            "areaHa": pol.get("areaHa"),
            "ativa": pol.get("ativa"),
            "localizacao": pol.get("localizacao"),
        }
        
        # Corrigir geo_shape: dados reais estão em poligonos.poligonos (não em geom)
        polygon_data = pol.get("poligonos")
        if polygon_data and isinstance(polygon_data, dict) and "coordinates" in polygon_data:
            shape["poligono"] = polygon_data
        
        # Substância e uso por polígono
        if pol.get("substancia"):
            shape["substancia"] = pol["substancia"]
        if pol.get("TipoUsoSubstancia") or pol.get("tipoUsoSubstancia"):
            shape["tipoUsoSubstancia"] = pol.get("TipoUsoSubstancia") or pol.get("tipoUsoSubstancia")
        
        # Fase do polígono (se diferente da raiz)
        if pol.get("faseProcesso"):
            shape["faseProcesso"] = pol["faseProcesso"]
        
        # Campos flat para busca sem nested
        sub = pol.get("substancia", {})
        if sub.get("nmSubstancia"):
            shape["shapeSubstancias"] = sub["nmSubstancia"]
        uso = pol.get("TipoUsoSubstancia") or pol.get("tipoUsoSubstancia") or {}
        if uso.get("dsTipoUsoSubstancia"):
            shape["shapeTipoUsoSubstancia"] = uso["dsTipoUsoSubstancia"]
        fase = pol.get("faseProcesso", {})
        if fase.get("dsFaseProcesso"):
            shape["shapeFaseProcesso"] = fase["dsFaseProcesso"]
        
        shapes.append(shape)
        
        # Capturar primeira localização para o root
        if first_localizacao is None and pol.get("localizacao"):
            loc = pol["localizacao"]
            if isinstance(loc, dict) and loc.get("lat") and loc.get("lon"):
                first_localizacao = loc
    
    if shapes:
        doc["shapes"] = shapes
    if first_localizacao:
        doc["localizacao"] = first_localizacao
    
    # ---- 3. Substâncias (FLAT no root + nested leve com vigência) ----
    # Flat: idSubstancias[], nmSubstancias[] → QPT nativo
    # Nested: substancias[] → vigência e encerramento por substância
    id_substancias = []
    nm_substancias = []
    substancias_nested = []
    for sub in source.get("substancias", []):
        substancia = sub.get("Substancia", {})
        id_sub = substancia.get("idSubstancia")
        nm_sub = substancia.get("nmSubstancia")
        if id_sub and id_sub not in id_substancias:
            id_substancias.append(id_sub)
        if nm_sub and nm_sub not in nm_substancias:
            nm_substancias.append(nm_sub)
        
        # Nested com objetos completos: substância, tipoUso, motivo, vigência
        sub_nested = {}
        # Substância (normaliza casing: Substancia → substancia)
        substancia_obj = sub.get("Substancia") or sub.get("substancia") or {}
        if substancia_obj:
            sub_nested["substancia"] = substancia_obj
        # Tipo de uso (normaliza casing: TipoUsoSubstancia → tipoUsoSubstancia)
        uso = sub.get("TipoUsoSubstancia") or sub.get("tipoUsoSubstancia") or {}
        if uso:
            sub_nested["tipoUsoSubstancia"] = uso
        # Motivo de encerramento (normaliza casing)
        motivo = sub.get("MotivoEncerramentoSubstancia") or sub.get("motivoEncerramentoSubstancia") or {}
        if isinstance(motivo, dict) and motivo.get("idMotivoEncerramentoSubstancia"):
            sub_nested["motivoEncerramentoSubstancia"] = motivo
        # Datas de vigência
        if sub.get("dtInicioVigencia"):
            sub_nested["dtInicioVigencia"] = sub["dtInicioVigencia"]
        if sub.get("dtFimVigencia"):
            sub_nested["dtFimVigencia"] = sub["dtFimVigencia"]
        
        if sub_nested:
            substancias_nested.append(sub_nested)
    
    if id_substancias:
        doc["idSubstancias"] = id_substancias
    if nm_substancias:
        doc["nmSubstancias"] = nm_substancias
    if substancias_nested:
        doc["substancias"] = substancias_nested
    
    # ---- 3b. Tipo de Uso (FLAT — extraído de shapes[].tipoUsoSubstancia) ----
    # Permite QPT filtrar por tipo de uso sem nested query
    # Ligação com anm_tipo_uso_substancia_v001 via idTipoUsoSubstancia
    id_tipo_usos = []
    ds_tipo_usos = []
    for pol in source.get("poligonos", []):
        uso = pol.get("TipoUsoSubstancia") or pol.get("tipoUsoSubstancia") or {}
        id_uso = uso.get("idTipoUsoSubstancia")
        ds_uso = uso.get("dsTipoUsoSubstancia")
        if id_uso and id_uso not in id_tipo_usos:
            id_tipo_usos.append(id_uso)
        if ds_uso and ds_uso not in ds_tipo_usos:
            ds_tipo_usos.append(ds_uso)
    if id_tipo_usos:
        doc["idTipoUsoSubstancias"] = id_tipo_usos
    if ds_tipo_usos:
        doc["dsTipoUsoSubstancias"] = ds_tipo_usos
    
    # ---- 4. Municípios (nested, corrige tipos) + FLAT siglasUF/nomesMunicipios ----
    municipios_v2 = []
    sigla_ufs = []
    nome_municipios = []
    for mun in source.get("municipios", []):
        mun_new = dict(mun)
        # Corrigir capitalUF: no v001 é int (0/1), no v002 é boolean
        if "capitalUF" in mun_new:
            mun_new["capitalUF"] = bool(mun_new["capitalUF"])
        # Corrigir amazoniaLegal: mesma situação
        if "amazoniaLegal" in mun_new:
            mun_new["amazoniaLegal"] = bool(mun_new["amazoniaLegal"])
        municipios_v2.append(mun_new)
        # Extrair para root (flat)
        uf = mun.get("siglaUF")
        nome = mun.get("nome")
        if uf and uf not in sigla_ufs:
            sigla_ufs.append(uf)
        if nome and nome not in nome_municipios:
            nome_municipios.append(nome)
    if municipios_v2:
        doc["municipios"] = municipios_v2
    if sigla_ufs:
        doc["siglasUF"] = sigla_ufs if len(sigla_ufs) > 1 else sigla_ufs[0]
    if nome_municipios:
        doc["nomesMunicipios"] = nome_municipios if len(nome_municipios) > 1 else nome_municipios[0]
    
    # ---- 5. Pessoas — REMOVE detalhesCNPJ, extrai cnpjBasico + FLAT nmTitulares/cnpjTitulares ----
    pessoas_v2 = []
    nm_titulares = []
    cnpj_titulares = []
    for pessoa_item in source.get("pessoas", []):
        pessoa_new = {}
        
        # Dados básicos da pessoa
        if pessoa_item.get("pessoa"):
            pessoa_new["pessoa"] = pessoa_item["pessoa"]
        
        # Extrair cnpjBasico de detalhesCNPJ como ID de ligação
        detalhes = pessoa_item.get("detalhesCNPJ", {})
        empresa = detalhes.get("empresa", {})
        if empresa.get("cnpjBasico"):
            pessoa_new["cnpjBasico"] = empresa["cnpjBasico"]
        
        # Relações e tipos
        # Nota: no v001 os campos estão com casing variado (TipoRelacao vs tipoRelacao)
        relacao_obj = None
        for campo in ["TipoRelacao", "tipoRelacao"]:
            if pessoa_item.get(campo):
                pessoa_new["tipoRelacao"] = pessoa_item[campo]
                relacao_obj = pessoa_item[campo]
                break
        
        for campo in ["tipoResponsabilidadeTecnica"]:
            if pessoa_item.get(campo):
                pessoa_new[campo] = pessoa_item[campo]
        
        for campo in ["tipoRepresentacaoLegal"]:
            if pessoa_item.get(campo):
                pessoa_new[campo] = pessoa_item[campo]
        
        # Datas
        for campo in ["dtInicioVigencia", "dtFimVigencia", "dtPrazoArrendamento"]:
            if pessoa_item.get(campo):
                pessoa_new[campo] = pessoa_item[campo]
        
        # NÃO copia detalhesCNPJ → essa é a otimização principal!
        # Para consultar detalhes do CNPJ: buscar cnpj_v001 por empresa.cnpjBasico
        
        # Extrair titulares para root (flat)
        ds_relacao = (relacao_obj or {}).get("dsTipoRelacao", "")
        if "titular" in ds_relacao.lower():
            pessoa_data = pessoa_item.get("pessoa", {})
            nm = pessoa_data.get("nmPessoa")
            cnpj = pessoa_new.get("cnpjBasico")
            if nm and nm not in nm_titulares:
                nm_titulares.append(nm)
            if cnpj and cnpj not in cnpj_titulares:
                cnpj_titulares.append(cnpj)
        
        pessoas_v2.append(pessoa_new)
    
    if pessoas_v2:
        doc["pessoas"] = pessoas_v2
    if nm_titulares:
        doc["nmTitulares"] = nm_titulares if len(nm_titulares) > 1 else nm_titulares[0]
    if cnpj_titulares:
        doc["cnpjTitulares"] = cnpj_titulares if len(cnpj_titulares) > 1 else cnpj_titulares[0]
    
    # ---- 6. Eventos — corrigir casing (Evento → evento) ----
    eventos_v2 = []
    for evt in source.get("eventos", []):
        evt_new = {}
        # Normalizar casing: "Evento" → "evento"
        evento_data = evt.get("Evento") or evt.get("evento")
        if evento_data:
            evt_new["evento"] = evento_data
        for campo in ["dtEvento", "obEvento", "dsPublicacaoDOU"]:
            if evt.get(campo):
                evt_new[campo] = evt[campo]
        eventos_v2.append(evt_new)
    if eventos_v2:
        doc["eventos"] = eventos_v2
    
    # ---- 7. Títulos — corrige casing TipoDocumentoLegal → tipoDocumentoLegal ----
    titulos_v2 = []
    for titulo in source.get("titulos", []):
        titulo_new = dict(titulo)
        # Normalizar casing: "TipoDocumentoLegal" → "tipoDocumentoLegal"
        if "TipoDocumentoLegal" in titulo_new:
            titulo_new["tipoDocumentoLegal"] = titulo_new.pop("TipoDocumentoLegal")
        titulos_v2.append(titulo_new)
    if titulos_v2:
        doc["titulos"] = titulos_v2
    
    # ---- 8. Associações (, sem mudança) ----
    if source.get("associacoes"):
        doc["associacoes"] = source["associacoes"]
    
    # ---- 9. Documentação → documentacoes (renomeia) ----
    docs_v2 = []
    for d in source.get("documentacao", []):
        doc_new = {}
        # Normalizar casing: "tipoDocumento " (com espaço no v001!) → "tipoDocumento"
        tipo = d.get("tipoDocumento ") or d.get("tipoDocumento")
        if tipo:
            doc_new["tipoDocumento"] = tipo
        if d.get("dtProtocolo"):
            doc_new["dtProtocolo"] = d["dtProtocolo"]
        docs_v2.append(doc_new)
    if docs_v2:
        doc["documentacoes"] = docs_v2
    
    # ---- 10. Propriedade do Solo (, sem mudança) ----
    if source.get("propriedadeSolo"):
        doc["propriedadeSolo"] = source["propriedadeSolo"]
    
    return doc


# =====================================================
# SCRIPT PRINCIPAL
# =====================================================

def create_client() -> OpenSearch:
    """Cria cliente OpenSearch."""
    return OpenSearch(
        hosts=[OPENSEARCH_ENDPOINT],
        http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
        use_ssl=True,
        verify_certs=True,
        timeout=60,
    )


def step1_delete_target(client: OpenSearch, force: bool = False):
    """Etapa 1: Deletar anm_v002 existente."""
    logger.info("=" * 60)
    logger.info("ETAPA 1: Deletar índice alvo (se existir)")
    logger.info("=" * 60)
    
    if client.indices.exists(index=TARGET_INDEX):
        count = client.count(index=TARGET_INDEX)["count"]
        logger.info(f"  Índice {TARGET_INDEX} existe com {count:,} docs")
        
        if count > 0 and not force:
            logger.warning(f"  ⚠️  {TARGET_INDEX} tem {count:,} docs! Deletar mesmo assim?")
            resp = input(f"  Digitar 'SIM' para deletar {TARGET_INDEX} com {count:,} docs: ")
            if resp.strip() != "SIM":
                logger.info("  Cancelado pelo usuário.")
                sys.exit(0)
        
        client.indices.delete(index=TARGET_INDEX)
        logger.info(f"  ✅ {TARGET_INDEX} deletado")
    else:
        logger.info(f"  {TARGET_INDEX} não existe — será criado")


def step2_create_mapping(client: OpenSearch):
    """Etapa 2: Criar anm_v002 com mapping otimizado."""
    logger.info("=" * 60)
    logger.info("ETAPA 2: Criar índice com mapping otimizado")
    logger.info("=" * 60)
    
    client.indices.create(index=TARGET_INDEX, body=ANM_V002_SETTINGS)
    logger.info(f"  ✅ {TARGET_INDEX} criado")
    
    # Verificar mapping
    mapping = client.indices.get_mapping(index=TARGET_INDEX)
    props = mapping[TARGET_INDEX]["mappings"]["properties"]
    logger.info(f"  Campos no root: {len(props)}")
    
    # Contar campos nested vs flat
    nested_count = sum(1 for p in props.values() if p.get("type") == "nested")
    logger.info(f"  Campos nested: {nested_count}")
    logger.info(f"  Campos flat: {len(props) - nested_count}")
    
    # Confirmar que detalhesCNPJ NÃO está no mapping
    pessoas_props = props.get("pessoas", {}).get("properties", {})
    has_detalhes = "detalhesCNPJ" in pessoas_props
    if has_detalhes:
        logger.error("  ❌ detalhesCNPJ ainda presente no mapping!")
        sys.exit(1)
    else:
        logger.info("  ✅ detalhesCNPJ REMOVIDO do mapping de pessoas")
    logger.info(f"  ✅ pessoas tem cnpjBasico como ID de ligação")


def step3_reindex(client: OpenSearch, dry_run: bool = False, limit: int | None = None):
    """Etapa 3-4: Scroll + Transform + Bulk Index."""
    logger.info("=" * 60)
    logger.info(f"ETAPA 3-4: Reindex {SOURCE_INDEX} → {TARGET_INDEX}")
    if dry_run:
        logger.info("  🔍 MODO DRY-RUN: nenhum dado será escrito")
    if limit:
        logger.info(f"  ⚠️ LIMITADO a {limit:,} documentos")
    logger.info("=" * 60)
    
    # Contagem total
    total_docs = client.count(index=SOURCE_INDEX)["count"]
    logger.info(f"  Total docs em {SOURCE_INDEX}: {total_docs:,}")
    
    if limit:
        total_docs = min(total_docs, limit)
        logger.info(f"  Processando: {total_docs:,} docs (limitado)")
    
    # Estatísticas
    stats = {
        "processed": 0,
        "indexed": 0,
        "errors": 0,
        "skipped": 0,
        "no_localizacao": 0,
        "no_poligonos": 0,
        "detalhes_removed": 0,
        "start_time": time.time(),
    }
    
    # Scroll
    bulk_actions = []
    
    scan_generator = helpers.scan(
        client,
        index=SOURCE_INDEX,
        scroll=SCROLL_TIMEOUT,
        size=SCROLL_SIZE,
        preserve_order=False,
    )
    
    for hit in scan_generator:
        if limit and stats["processed"] >= limit:
            break
        
        source = hit["_source"]
        doc_id = hit["_id"]
        stats["processed"] += 1
        
        try:
            # Transform
            new_doc = transform_document(source)
            
            # Contabilizar
            if not new_doc.get("localizacao"):
                stats["no_localizacao"] += 1
            if not new_doc.get("shapes"):
                stats["no_poligonos"] += 1
            
            # Verificar se detalhesCNPJ foi removido
            for pessoa in new_doc.get("pessoas", []):
                if "detalhesCNPJ" not in pessoa:
                    stats["detalhes_removed"] += 1
            
            if not dry_run:
                bulk_actions.append({
                    "_index": TARGET_INDEX,
                    "_id": doc_id,
                    "_source": new_doc,
                })
            
            # Bulk flush
            if len(bulk_actions) >= BULK_SIZE:
                if not dry_run:
                    success, errors = helpers.bulk(
                        client,
                        bulk_actions,
                        raise_on_error=False,
                        stats_only=True,
                    )
                    stats["indexed"] += success
                    stats["errors"] += errors
                bulk_actions.clear()
            
            # Log de progresso
            if stats["processed"] % 10000 == 0:
                elapsed = time.time() - stats["start_time"]
                rate = stats["processed"] / elapsed if elapsed > 0 else 0
                eta_seconds = (total_docs - stats["processed"]) / rate if rate > 0 else 0
                eta_min = eta_seconds / 60
                pct = (stats["processed"] / total_docs) * 100
                logger.info(
                    f"  📊 {stats['processed']:>9,} / {total_docs:,} ({pct:.1f}%) "
                    f"| {rate:.0f} docs/s "
                    f"| ETA: {eta_min:.1f} min "
                    f"| erros: {stats['errors']}"
                )
        
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 10:
                logger.error(f"  ❌ Erro no doc {doc_id}: {e}")
            elif stats["errors"] == 11:
                logger.error("  ... suprimindo erros adicionais")
    
    # Flush remaining
    if bulk_actions and not dry_run:
        success, errors = helpers.bulk(
            client,
            bulk_actions,
            raise_on_error=False,
            stats_only=True,
        )
        stats["indexed"] += success
        stats["errors"] += errors
    
    # Refresh
    if not dry_run:
        logger.info(f"  Refreshing {TARGET_INDEX}...")
        client.indices.refresh(index=TARGET_INDEX)
    
    # Resultado final
    elapsed = time.time() - stats["start_time"]
    
    logger.info("=" * 60)
    logger.info("RESULTADO FINAL")
    logger.info("=" * 60)
    logger.info(f"  Processados:       {stats['processed']:>10,}")
    logger.info(f"  Indexados:         {stats['indexed']:>10,}")
    logger.info(f"  Erros:             {stats['errors']:>10,}")
    logger.info(f"  Sem localização:   {stats['no_localizacao']:>10,}")
    logger.info(f"  Sem polígonos:     {stats['no_poligonos']:>10,}")
    logger.info(f"  detalhesCNPJ rem.: {stats['detalhes_removed']:>10,}")
    logger.info(f"  Tempo total:       {elapsed/60:>10.1f} min")
    logger.info(f"  Taxa média:        {stats['processed']/elapsed:>10.0f} docs/s")
    
    if not dry_run:
        # Verificar contagem final
        final_count = client.count(index=TARGET_INDEX)["count"]
        logger.info(f"  Docs em {TARGET_INDEX}: {final_count:,}")
        
        # Comparar tamanho
        v1_stats = client.indices.stats(index=SOURCE_INDEX)["indices"][SOURCE_INDEX]["total"]["store"]
        v2_stats = client.indices.stats(index=TARGET_INDEX)["indices"][TARGET_INDEX]["total"]["store"]
        v1_size = v1_stats["size_in_bytes"]
        v2_size = v2_stats["size_in_bytes"]
        reduction = ((v1_size - v2_size) / v1_size) * 100 if v1_size > 0 else 0
        logger.info(f"  Tamanho {SOURCE_INDEX}: {v1_size / (1024**3):.2f} GB")
        logger.info(f"  Tamanho {TARGET_INDEX}: {v2_size / (1024**3):.2f} GB")
        logger.info(f"  Redução: {reduction:.1f}%")
    
    logger.info("=" * 60)
    if stats["errors"] > 0:
        logger.warning(f"⚠️  Houve {stats['errors']} erros. Verifique o log.")
    else:
        logger.info("✅ Reindex concluído com sucesso!")


def main():
    parser = argparse.ArgumentParser(description="Reindex anm_v001 → anm_v002")
    parser.add_argument("--dry-run", action="store_true", help="Não escreve, só valida")
    parser.add_argument("--limit", type=int, default=None, help="Limitar docs (para teste)")
    parser.add_argument("--skip-create", action="store_true", help="Pular criação do índice")
    parser.add_argument("--force", action="store_true", help="Não pedir confirmação para deletar")
    args = parser.parse_args()
    
    logger.info("🚀 Iniciando reindex anm_v001 → anm_v002")
    logger.info(f"   Dry-run: {args.dry_run}")
    logger.info(f"   Limit: {args.limit or 'sem limite'}")
    
    client = create_client()
    
    # Verificar conexão
    info = client.info()
    logger.info(f"   Cluster: {info['cluster_name']}")
    logger.info(f"   Versão: {info['version']['number']}")
    
    if not args.skip_create:
        step1_delete_target(client, force=args.force)
        step2_create_mapping(client)
    
    step3_reindex(client, dry_run=args.dry_run, limit=args.limit)
    
    client.close()


if __name__ == "__main__":
    main()
