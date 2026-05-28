# Catálogo de Cenários e Perguntas Pertinentes — MineralRadar

> **Versão:** 1.0 · 12 de maio de 2026
> **Objetivo:** banco de perguntas reais, objetivas e por cenário, alinhadas
> às rotas de intenção do agente, aos módulos MCP, às fontes de dados
> indexadas e aos perfis de usuário do MineralRadar.
> **Uso recomendado:** testes manuais, demos guiadas, validação de regressão
> do roteador (`backend/app/langgraph/router.py`), suíte de avaliação do
> LLM, scripts de smoke test e conteúdo de onboarding.

---

## Sumário

1. [Como ler este documento](#1-como-ler-este-documento)
2. [Cenários por Rota do Agente](#2-cenários-por-rota-do-agente)
   - [2.1 `mineral`](#21-rota-mineral)
   - [2.2 `empresa`](#22-rota-empresa)
   - [2.3 `hybrid`](#23-rota-hybrid)
   - [2.4 `geo`](#24-rota-geo)
   - [2.5 `mineral_em_isocrona`](#25-rota-mineral_em_isocrona)
   - [2.6 `empresa_em_isocrona`](#26-rota-empresa_em_isocrona)
   - [2.7 `hibrido_em_isocrona`](#27-rota-hibrido_em_isocrona)
   - [2.8 `general` (saudações, escopo, recusas)](#28-rota-general)
3. [Cenários por Módulo Funcional](#3-cenários-por-módulo-funcional)
   - [Inteligência Mineral (Jazidas)](#31-inteligência-mineral-jazidas)
   - [Inteligência Empresarial (CNPJ/CNAE)](#32-inteligência-empresarial-cnpjcnae)
   - [Compliance e Restrições Ambientais](#33-compliance-e-restrições-ambientais)
   - [CFEM e Royalties](#34-cfem-e-royalties)
   - [Geologia e Ocorrências (CPRM/SGB)](#35-geologia-e-ocorrências-cprmsgb)
   - [Geoquímica CPRM (análises de rocha e mineral/minério)](#35b-geoquímica-cprm-análises-de-rocha-e-mineralminério)
   - [Mercado e Comércio Exterior](#36-mercado-e-comércio-exterior)
   - [Logística e Geoespacial](#37-logística-e-geoespacial)
   - [Monitoramento e Alertas](#38-monitoramento-e-alertas)
4. [Cenários por Perfil de Usuário](#4-cenários-por-perfil-de-usuário)
5. [Cenários por Substância](#5-cenários-por-substância)
6. [Cenários por Fase do Processo ANM](#6-cenários-por-fase-do-processo-anm)
7. [Cenários de Workspace (Projeto, Memória, Histórico)](#7-cenários-de-workspace-projeto-memória-histórico)
8. [Cenários de Borda (edge cases, ambiguidade, recusa)](#8-cenários-de-borda)
9. [Checklist de Cobertura](#9-checklist-de-cobertura)

---

## 1. Como ler este documento

- Cada cenário possui **objetivo**, **rota esperada** do roteador e
  **perguntas** que o usuário tipicamente formula em português coloquial.
- A coluna **rota esperada** corresponde aos valores definidos em
  `ROUTE_CONFIGS` (ver `backend/app/langgraph/router.py`): `mineral`,
  `empresa`, `hybrid`, `geo`, `general`, `mineral_em_isocrona`,
  `empresa_em_isocrona`, `hibrido_em_isocrona`.
- Quando uma pergunta exige **mais de uma chamada de ferramenta**, o
  encadeamento esperado é indicado entre parênteses.
- Variações regionais e sinônimos foram preservados (ex.: "brita" /
  "agregado graúdo", "lítio" / "Li" / "espodumênio").

---

## 2. Cenários por Rota do Agente

### 2.1 Rota `mineral`

> Foco em jazidas, processos ANM, substâncias, vigência, fases e CFEM
> específico de um processo.
> Ferramentas: `jazidas__*` + `geo__buscar_municipio`.

| # | Pergunta | Encadeamento esperado |
|---|----------|------------------------|
| M1 | "Quais são as jazidas de lítio ativas em Minas Gerais?" | `buscar_jazidas(substancia="lítio", uf="MG")` |
| M2 | "Mostre os processos de nióbio em concessão de lavra no Goiás." | `buscar_jazidas(substancia="nióbio", fase="concessão", uf="GO")` |
| M3 | "Há processos ANM de areia em Sete Lagoas/MG?" | `buscar_municipio` → `buscar_jazidas(substancia="areia", municipio=...)` |
| M4 | "Quem é o titular do processo 871.269/2024?" | `detalhes_processo(ds_processo="871.269/2024")` |
| M5 | "Esse processo está vigente?" (follow-up) | `verificar_vigencia` |
| M6 | "Liste todos os processos de calcário com mais de 100 hectares na Bahia." | `buscar_jazidas(substancia="calcário", uf="BA", area_min_ha=100)` |
| M7 | "Quais processos de grafita estão em fase de requerimento de pesquisa?" | `buscar_jazidas(substancia="grafita", fase="requerimento de pesquisa")` |
| M8 | "Quais são os processos de terras raras no Brasil ordenados por área?" | `buscar_jazidas(substancia="terras raras")` |
| M9 | "Mostre as áreas em disponibilidade de cobre em Pará." | `areas_em_disponibilidade(substancia="cobre", uf="PA")` |
| M10 | "Quais minas de bauxita estão produzindo (CFEM nos últimos 12 meses) em Minas Gerais?" | `buscar_jazidas` + `consultar_cfem_processo` |
| M11 | "Há sobreposição de processos de fosfato no município de Catalão/GO?" | `buscar_municipio` → `jazidas_por_poligono(substancia="fosfato")` |
| M12 | "Quais substâncias são extraídas no processo 832.500/2018?" | `detalhes_processo` |
| M13 | "Quais são as ocorrências CPRM de cobalto no Mato Grosso?" | `ocorrencias_minerais_proximas(substancia="cobalto", uf="MT")` |
| M14 | "O processo 880.111/2020 sobrepõe alguma TI ou UC?" | `buscar_restricoes_geo(ds_processo=...)` |
| M15 | "Há afloramentos geológicos próximos do processo 855.250/2019?" | `detalhes_processo` → `afloramentos_geologicos_proximos` |

---

### 2.2 Rota `empresa`

> Empresas comerciais por atividade econômica (CNAE) — produtos
> industrializados, transportadoras, escritórios, etc. Sem mineração direta.
> Ferramentas: `empresas__*` + `geo__buscar_municipio`.

| # | Pergunta | Encadeamento esperado |
|---|----------|------------------------|
| E1 | "Cimenteiras em Belo Horizonte." | `buscar_municipio` → `buscar_empresas(termo_busca="cimento")` |
| E2 | "Pré-moldados em Contagem/MG, as maiores." | `buscar_empresas(..., ordenar_por="capital_social")` |
| E3 | "Transportadoras de carga pesada num raio de 80 km de Sete Lagoas." | `buscar_empresas(termo_busca="transportadora", raio_km=80)` |
| E4 | "Quem é o CNPJ 17.281.106/0001-03?" | `detalhes_empresa(cnpj=...)` |
| E5 | "Quais empresas o sócio CPF ***.456.***-** controla?" | `buscar_por_socio(cpf_cnpj=...)` |
| E6 | "Tem multa do IBAMA contra a Vale S.A.?" | `risco_ambiental_empresa(cnpj_basico="33.592.510")` |
| E7 | "Quais empresas têm autuação ambiental num raio de 50 km de Parauapebas/PA?" | `autuacoes_por_area(lat, lon, raio_km=50)` |
| E8 | "Concreteiras em Goiânia ordenadas por capital social." | `buscar_empresas(termo_busca="concreto", municipio="Goiânia", ordenar_por="capital_social")` |
| E9 | "Fabricantes de explosivos para mineração no Brasil." | `buscar_empresas(termo_busca="explosivos para mineração")` |
| E10 | "Empresas de pavimentação asfáltica em Salvador/BA, ativas." | `buscar_empresas(termo_busca="pavimentação asfáltica", municipio="Salvador")` |
| E11 | "Qual o quadro societário da Companhia Brasileira de Metalurgia e Mineração?" | `detalhes_empresa` |
| E12 | "Tem ferragem aberta após 2020 num raio de 30 km de Contagem?" | `buscar_empresas(..., abertura_apos="2020-01-01")` |
| E13 | "Há laboratórios de análise mineral no Triângulo Mineiro?" | `buscar_empresas(termo_busca="laboratório análise mineral")` |
| E14 | "Empresas de drone para topografia em São Paulo capital." | `buscar_empresas(termo_busca="drone topografia")` |

---

### 2.3 Rota `hybrid`

> Combina **jazidas (extrato bruto)** e **empresas (industrializado/serviço)**
> na mesma resposta. Crítica a classificação por material.

| # | Pergunta | Encadeamento esperado |
|---|----------|------------------------|
| H1 | "Quem produz e quem compra grafita no Tocantins?" | `buscar_fornecedores("grafita")` + `buscar_empresas(termo_busca="grafita industrial")` |
| H2 | "Preciso de areia, brita e cimento perto da obra em Lagoa Santa/MG." | `buscar_fornecedores("areia")` + `buscar_fornecedores("brita")` + `buscar_empresas("cimento")` |
| H3 | "Toda a cadeia de calcário em Sete Lagoas/MG — extração e cal hidratada." | `buscar_fornecedores("calcário")` + `buscar_empresas("cal hidratada")` |
| H4 | "Fornecedores de saibro e argamassa em Goiânia." | `buscar_fornecedores("saibro")` + `buscar_empresas("argamassa")` |
| H5 | "Empresas que extraem e empresas que beneficiam fosfato em Patrocínio/MG." | `buscar_fornecedores("fosfato")` + `buscar_empresas("beneficiamento fosfato")` |
| H6 | "Quem fornece areia industrial para vidro no estado do Rio de Janeiro?" | `buscar_fornecedores("areia industrial")` + `buscar_empresas("vidro")` |
| H7 | "Mostre toda a cadeia de ferro em Carajás (mina + porto + transporte)." | `buscar_fornecedores("ferro", uf="PA")` + `buscar_empresas("logística portuária")` |
| H8 | "Concreteiras com mina de areia própria em Belo Horizonte." | `buscar_empresas("concreto")` + cross com `buscar_fornecedores("areia")` por CNPJ |
| H9 | "Quem produz lítio no Brasil e quem exporta?" | `buscar_fornecedores("lítio")` + `buscar_empresas("exportação lítio")` |
| H10 | "Areia, brita, cimento e ferragem num raio de 50 km da obra." | composição completa |

---

### 2.4 Rota `geo`

> Consultas puramente geográficas — rotas, isócronas, distâncias,
> geocodificação, plotagem de pontos.

| # | Pergunta | Encadeamento esperado |
|---|----------|------------------------|
| G1 | "Calcule a rota de caminhão da Mina do Salobo até o Porto de Itaqui." | `calcular_rota(modo="truck")` |
| G2 | "Quantos km do processo 870.123/2021 até a Ferrovia Norte-Sul mais próxima?" | `detalhes_processo` → `calcular_rota` |
| G3 | "Compare a rota até os portos de Santos, Paranaguá e Itaqui a partir de Catalão/GO." | `comparar_rotas(origem, [destino_santos, destino_paranagua, destino_itaqui])` |
| G4 | "Em quanto tempo chego em Brumadinho saindo de BH?" | `calcular_rota` |
| G5 | "Geocodifique 'Av. Paulista, 1578, São Paulo'." | `geocodificar` |
| G6 | "Qual o município das coordenadas -19.93, -44.05?" | `municipio_por_coordenada` |
| G7 | "Mostre no mapa o endereço 'Rua dos Geólogos, 100, Ouro Preto/MG'." | `plotar_endereco` |
| G8 | "Em que bioma está o ponto -10.5, -55.0?" | `bioma_por_coordenada` |
| G9 | "Em qual província mineral fica Catalão/GO?" | `buscar_municipio` → `provincia_por_coordenada` |
| G10 | "Municípios num raio de 100 km de Parauapebas/PA." | `municipios_em_raio(raio_km=100)` |
| G11 | "Há imóveis SIGEF certificados próximos ao processo 875.001/2023?" | `detalhes_processo` → `imoveis_rurais_em_area` |
| G12 | "Trace a rota mais curta entre os 3 processos do meu projeto." | `comparar_rotas` |

---

### 2.5 Rota `mineral_em_isocrona`

> Critério é **tempo de viagem** ("X minutos/horas de caminhão"), não km.
> Tool atômica: `geo__buscar_dentro_de_isocrona(substancia=..., criterio="tempo")`.

| # | Pergunta | Observação |
|---|----------|-----------|
| MI1 | "Quais jazidas de areia em até 60 min de caminhão da obra?" | origem = obra do projeto |
| MI2 | "Processos de brita a 90 minutos de Sete Lagoas/MG." | origem = município |
| MI3 | "Processos de calcário acessíveis em 2 horas a partir do processo 832.100/2017." | origem = jazida (precedência: detalhes_processo) |
| MI4 | "Que jazidas de cobalto existem a 4h de Manaus?" | exemplo do whitepaper |
| MI5 | "Tudo o que tem dentro da isócrona violeta de 45 min." | herda contexto da isócrona |
| MI6 | "Pedreiras na isócrona de uma hora da obra." | criterio=tempo, valor=60 |
| MI7 | "Areia e cascalho até 30 min do depósito atual." | tempo curto |

---

### 2.6 Rota `empresa_em_isocrona`

| # | Pergunta | Observação |
|---|----------|-----------|
| EI1 | "Fornecedores de cimento em 60 min de caminhão da obra." | termo_busca="cimento", tempo=60 |
| EI2 | "Pré-moldados em 90 minutos a partir de Contagem/MG." | termo_busca="pré-moldados" |
| EI3 | "Transportadoras de carga pesada na isócrona de 2h do porto." | tempo=120 |
| EI4 | "Fornecedores de explosivos em até 3 horas de Carajás." | substância em contexto industrial → termo_busca |
| EI5 | "Laboratórios de análise química em 45 min da mina." | termo_busca="laboratório análise química" |
| EI6 | "Empresas com CNAE 23.30-3 (pré-moldados) em 1h de SP capital." | codigos_cnae direto |

---

### 2.7 Rota `hibrido_em_isocrona`

| # | Pergunta | Observação |
|---|----------|-----------|
| HI1 | "Areia, brita e cimento em até 1h da obra." | substancia + termo_busca juntos |
| HI2 | "Calcário (mina) e cal hidratada (indústria) em 90 min de Sete Lagoas." | combo bruto + industrializado |
| HI3 | "Tudo que tenho de fornecedor mineral e industrial em 2h da obra." | open-ended |
| HI4 | "Cadeia completa de fosfato em 3 horas de Patrocínio/MG." | substancia + termo_busca |
| HI5 | "Fornecedores de saibro e empresas de argamassa em 45 min." | combo |

---

### 2.8 Rota `general`

> Saudações, perguntas sobre a plataforma e **recusas educadas** para temas
> fora de escopo.

#### Em escopo (responder com cordialidade)

- "Olá, o que o MineralRadar faz?"
- "Como funciona a busca por isócrona?"
- "Quais fontes de dados vocês usam?"
- "Posso exportar um relatório em PDF?"
- "Como adiciono um processo ao meu projeto?"

#### Fora de escopo (recusar em 1 frase, ofertar ajuda mineral)

- "Qual a cotação do dólar hoje?"
- "Como está o tempo em Brasília?"
- "Me dá uma receita de bolo."
- "Quem ganhou o jogo do Flamengo ontem?"
- "Preço da ação da Vale agora." *(refusar — não somos terminal financeiro;
  podemos oferecer dados públicos da CVM/B3 como apoio à due diligence,
  mas não cotação intraday.)*
- "Qual o resultado das eleições municipais?"
- "Me ajuda a programar em Python?"

---

## 3. Cenários por Módulo Funcional

### 3.1 Inteligência Mineral (Jazidas)

> Foco em **`mr_jazidas_v001`** (~907K docs SIGMINE geo + enriquecimentos) e índices
> derivados (`mr_substancias_v001`, `mr_cfem_v001`).

| Cenário | Pergunta exemplo |
|--------|------------------|
| Busca por substância e UF | "Quantos processos de manganês existem em Minas Gerais?" |
| Busca por CNAE de titular | "Listar processos cujo titular tenha CNAE 07.29-4 (extração de minerais não-ferrosos não especificados)." |
| Busca por área mínima | "Concessões de ouro com mais de 500 ha no Pará." |
| Busca por inativos | "Quais processos de cassiterita foram extintos em Rondônia desde 2015?" |
| Busca semântica de substância | "Processos relacionados a baterias de veículos elétricos." *(esperado: lítio + grafita + cobalto + níquel)* |
| Detalhes completos | "Tudo sobre o processo 800.123/2019." |
| Vigência de título | "O processo 850.555/2020 ainda está vigente? Qual o prazo?" |
| Polígono customizado | "Quais processos estão dentro deste GeoJSON que desenhei?" |
| Histórico de produção | "Houve produção declarada (CFEM) no processo 860.001/2018 nos últimos 5 anos?" |
| Áreas em disponibilidade | "Áreas em disponibilidade na próxima rodada de leilão da ANM." |

---

### 3.2 Inteligência Empresarial (CNPJ/CNAE)

> Foco em **`mr_empresas_v001`** (~350k empresas filtradas por relevância
> mineral) + `mr_cnae_v001` (2.394 CNAEs com k-NN).

| Cenário | Pergunta exemplo |
|--------|------------------|
| Busca semântica por CNAE | "Empresas que produzem ímãs permanentes de neodímio." |
| Filtro por raio | "Concreteiras num raio de 30 km de Lagoa Santa/MG." |
| Filtro por porte | "As 10 maiores cimenteiras do Brasil por capital social." |
| Filtro por situação | "Empresas com CNAE 07.21-9 (extração de minério de alumínio) que tiveram baixa após 2020." |
| Quadro societário | "Quem são os sócios da Companhia Brasileira de Lítio?" |
| Rede societária | "Outras empresas controladas pelos sócios da Nexa Resources." |
| Risco ambiental | "Quantas autuações IBAMA o CNPJ 33.000.167/0001-01 tem? Total de multa?" |
| Autuações por área | "Empresas autuadas pelo IBAMA num raio de 100 km de Manaus." |
| Cross-walk ANM ↔ CNPJ | "Em quantos processos ANM a empresa Nexa Recursos Minerais é titular?" |
| Capital + autuação | "Empresas de mineração com capital > R$ 10M e mais de 3 autuações IBAMA." |

---

### 3.3 Compliance e Restrições Ambientais

> Índices: `funai_tis`, `ibama_ucs`, `sicar_car`, `ibge_biomas`,
> `sigef_incra`, `mr_autuacoes_v001`.

| Cenário | Pergunta exemplo |
|--------|------------------|
| Sobreposição com TI | "Esse processo cruza com alguma Terra Indígena?" |
| TI em estudo | "Tem TI em estudo dentro do meu polígono?" |
| Sobreposição com UC | "O processo 875.250/2023 sobrepõe Unidades de Conservação?" |
| APA permite mineração restrita | "Quais processos ativos cruzam Áreas de Proteção Ambiental no Pará?" |
| SICAR | "Há imóveis CAR sobre o polígono do meu processo? Quem são os proprietários?" |
| INCRA SIGEF | "Imóveis rurais certificados num raio de 20 km da obra." |
| Bioma | "Esse ponto está no Cerrado ou na Amazônia?" |
| Autuações no CNPJ | "Resumo das autuações IBAMA da Vale (CNPJ 33.592.510)." |
| Score de risco agregado | "Dê o nível de risco ambiental do processo 880.111/2024." |
| Ranking de risco por estado | "Estados com maior número de processos sobrepostos a TIs em estudo." |

---

### 3.4 CFEM e Royalties

> Índice: `mr_cfem_v001` (série mensal desde 2010).

| Cenário | Pergunta exemplo |
|--------|------------------|
| CFEM por processo | "Quanto o processo 832.100/2017 pagou de CFEM em 2025?" |
| CFEM por empresa | "Quais foram os 10 maiores arrecadadores de CFEM em 2025?" |
| CFEM por município | "Município brasileiro que mais arrecadou CFEM em 2024." |
| Tendência temporal | "Evolução da CFEM de nióbio no Brasil entre 2018 e 2025." |
| CFEM por substância | "Top 5 substâncias em arrecadação CFEM em 2025." |
| Distribuição estadual | "Distribuição da CFEM de minério de ferro por UF em 2024." |
| Comparativo de empresas | "Comparar CFEM da Vale, CSN e Anglo American nos últimos 3 anos." |
| Produção real vs declarada | "Processos com RAL declarado mas CFEM zero nos últimos 12 meses." |
| Decadência | "Quais processos perderam vigência por falta de pagamento de CFEM?" |
| Sazonalidade | "Há padrão sazonal na arrecadação CFEM de areia em Minas Gerais?" |

---

### 3.5 Geologia e Ocorrências (CPRM/SGB)

> Índices: `mr_cprm_v001` (~36K ocorrências minerais GeoBank) + `mr_geoquimica_v001`
> (~65K análises geoquímicas) + afloramentos via OGC API on-demand.
> Tools: `ocorrencias_minerais_proximas`, `geoquimica_proxima`,
> `afloramentos_geologicos_proximos`.

| Cenário | Pergunta exemplo |
|--------|------------------|
| Ocorrências sem processo | "Onde há ocorrências de cobalto registradas pelo CPRM sem processo ANM associado?" |
| Tipo de depósito | "Mostre depósitos do tipo IOCG no Brasil." |
| Província mineral | "Quais ocorrências fazem parte da Província Mineral de Carajás?" |
| Cruzamento ANM × CPRM | "Processos ativos sobrepostos a ocorrências de terras raras do CPRM." |
| Afloramentos | "Há afloramentos de pegmatito a 10 km do processo 871.269/2024?" |
| Mapa geológico | "Qual unidade geológica está sob este polígono?" |
| Teor declarado | "Ocorrências de fosfato com teor > 20% P2O5." |
| Hidrogeologia | "Há poços tubulares SIAGAS na área do processo?" |
| Reativação | "Áreas onde há ocorrência CPRM e processo extinto recente (potencial de reativação)." |

---

### 3.5b Geoquímica CPRM (análises de rocha e mineral/minério)

> Índice: `mr_geoquimica_v001` (61K amostras de rocha + 4K de mineral/minério,
> ~65K docs com nested analises por elemento). Tool: `geoquimica_proxima`.

| Cenário | Pergunta exemplo |
|--------|------------------|
| Teores de ouro na área | "Qual o teor de ouro nas amostras de rocha a 50 km de Paracatu/MG?" |
| Anomalia geoquímica de cobre | "Há anomalias de cobre nas análises CPRM próximas ao processo 830.123/2019?" |
| Nióbio no Planalto Central | "O CPRM analisou amostras com Nb nessa área de Goiás?" |
| Terras raras — teores reais | "Amostras com Ce, La ou Nd detectados próximos a Araxá/MG." |
| Multi-elemento estratégico | "Análises com Au > 0.1 ppm nas rochas de um raio de 30 km de Crixás/GO?" |
| Contexto de projeto de pesquisa | "Quais projetos CPRM coletaram amostras na área da isócrona?" |
| Mineral/minério — teor elevado | "Amostras de mineral/minério com Co > 1% catalogadas pelo CPRM?" |
| Comparar CPRM × processo ANM | "O processo de pesquisa 860.500/2022 tem amostras geoquímicas de Au na área?" |
| Elementos associados | "Quais outros elementos foram detectados nas amostras com Nb em Goiás?" |
| Dado histórico | "Há análises geoquímicas do projeto Adrianópolis/PR? Quais elementos foram medidos?" |
| Verificação de tório/urânio | "Há detecção de U ou Th nas rochas da área do processo X?" |
| Análise de pegmatito | "Amostras de rocha com Li ou Ta próximas ao Vale do Jequitinhonha (MG)." |

---

### 3.6 Mercado e Comércio Exterior

> Fontes: ComexStat/MDIC, Metals-API, CVM/B3, USGS MRDS.

| Cenário | Pergunta exemplo |
|--------|------------------|
| Exportação por NCM | "Volume e valor FOB exportado de nióbio (NCM 2615.90) em 2025." |
| Destinos principais | "Para onde o Brasil exporta grafita natural?" |
| Importação | "Quanto o Brasil importou de cobalto refinado em 2024?" |
| Preço internacional | "Qual o preço atual de carbonato de lítio?" |
| Série histórica de preço | "Preço médio mensal de NdPr nos últimos 24 meses." |
| Correlação preço × produção | "A produção brasileira de lítio acompanhou a alta do preço internacional desde 2022?" |
| Empresas listadas | "Mineradoras brasileiras listadas na B3 com operação em ferro." |
| Junior miners | "Empresas estrangeiras com projetos de TR no Brasil — controladoras." |
| Tendência global | "Qual a perspectiva de demanda de grafita para baterias até 2030?" |

---

### 3.7 Logística e Geoespacial

> Azure Maps (rotas, isócronas), ANTAQ, ANTT, registry de portos.

| Cenário | Pergunta exemplo |
|--------|------------------|
| Rota mina → porto | "Caminho de caminhão da mina do Salobo ao Porto de Vila do Conde." |
| Comparar portos | "Compare frete da mina X aos 3 portos mais próximos." |
| Isócrona de exportação | "O que está a 6h de caminhão do Porto de Paranaguá?" |
| Distância a ferrovia | "Distância do processo 860.001/2018 à Estrada de Ferro Carajás." |
| Acessibilidade off-road | "A rota até esse processo tem trecho não pavimentado?" |
| Geocodificação reversa | "Endereço completo das coordenadas -19.5, -45.2." |
| Múltiplos depósitos | "Plotar 5 depósitos no mapa e ligá-los por rota otimizada." |
| Porto mais próximo | "Porto marítimo mais próximo do município de Vila Pavão/ES." |

---

### 3.8 Monitoramento e Alertas

> Índices: `mr_monitoring_v001`, DOU API (in.gov.br), prazos SCM/SICOP.

| Cenário | Pergunta exemplo |
|--------|------------------|
| Resumo da carteira | "Resumo dos alertas da minha carteira hoje." |
| Prazos vencendo | "Quais dos meus processos têm prazo SCM vencendo em 30 dias?" |
| DOU diário | "Publicações do DOU de hoje envolvendo meus processos." |
| Cancelamentos | "Houve cancelamento ou caducidade entre meus processos esta semana?" |
| Mudança de fase | "Algum processo do projeto mudou de fase nos últimos 7 dias?" |
| Novas autuações | "Foi lançada nova autuação IBAMA contra alguma empresa do meu watchlist?" |
| Alerta de áreas em disponibilidade | "Saiu edital de área de cobre disponível esta semana?" |

---

## 4. Cenários por Perfil de Usuário

### 4.1 Geólogo / Exploração

- "Onde há ocorrências de terras raras em pegmatitos no Brasil?"
- "Áreas em disponibilidade de cobre no Pará dentro da Província de Carajás."
- "Processos de pesquisa em fase final que liberam área em 2026."
- "Indícios geológicos de grafita não cobertos por processo ativo."
- "Teores de Au nas análises geoquímicas CPRM a 20 km do processo 871.269/2024."
- "Quais projetos CPRM cobriram a área de interesse com análises de rocha?"
- "Afloramentos de pegmatito + amostras com Li ou Ta nessa região."

### 4.2 Engenheiro / Compras de Obra (Construção Civil)

- "Areia, brita e cimento a 60 min da obra em Lagoa Santa/MG."
- "As 5 concreteiras mais próximas da obra com capital social acima de R$ 5M."
- "Pedreiras de basalto a 100 km da BR-040 (km 580)."
- "Transportadoras com frota acima de 20 caminhões em Sete Lagoas."

### 4.3 Comprador Industrial

- "Fornecedores de espodumênio (lítio) no Brasil ativos."
- "Empresas que beneficiam fosfato e estão a 6h do Porto de Santos."
- "Onde comprar argila refratária com qualidade industrial em Minas Gerais?"
- "Produtores de ferronióbio no Brasil — top 3."

### 4.4 M&A / Investidor / Banker

- "Due diligence rápida do processo 832.100/2017: titular, CFEM, autuações,
  restrições, vigência."
- "Empresas com capital social > R$ 50M, mais de 5 processos titulados e
  zero autuações ambientais nos últimos 24 meses."
- "Junior miners de lítio listadas em TSX com projetos no Brasil."
- "Histórico CFEM dos 10 maiores titulares de processos de nióbio."

### 4.5 Consultor Ambiental

- "Quais processos no estado do Pará sobrepõem TIs homologadas?"
- "Processos com mais de 3 autuações IBAMA por embargo nos últimos 5 anos."
- "Sobreposição entre o polígono do processo X e o CAR/SICAR da fazenda Y."
- "Empresas com histórico de derrame em rejeitos."

### 4.6 Trader / Exportador

- "Para onde o Brasil exporta ímãs permanentes (NCM 8505.11)?"
- "Quais empresas brasileiras exportam ferronióbio direto para a China?"
- "Preço FOB médio do óxido de neodímio nos últimos 12 meses."
- "Empresas em raio de 6h do Porto de Itaqui com CNAE de exportação mineral."

### 4.7 Gestor Público / Órgão

- "Top 10 municípios em arrecadação de CFEM no Maranhão em 2025."
- "Concentração de titularidade: qual % dos processos da BA pertence aos
  3 maiores titulares?"
- "Processos com prazos SCM vencidos há mais de 90 dias."
- "Distribuição de processos ativos por bioma."

### 4.8 Advogado / Compliance Minerário

- "Listar atos publicados no DOU para o processo 855.250/2019 nos últimos
  90 dias."
- "Processos do meu cliente com pendência administrativa SICOP."
- "Histórico de autos de infração contra o CNPJ 12.345.678/0001-90."
- "Áreas em litígio: processos com múltiplos requerimentos sobrepostos."

---

## 5. Cenários por Substância

### Minerais críticos / estratégicos

- "Mapa de lítio no Brasil (todos os processos ativos)."
- "Quem detém processos de nióbio fora de Minas Gerais e Goiás?"
- "Grafita: produção brasileira e principais destinos de exportação."
- "Cobalto: ocorrências CPRM × processos ANM × teores geoquímicos."
- "Terras raras: identificar processos com substâncias do grupo (La, Ce, Nd, Pr, Dy, Tb)."
- "Urânio: titularidade exclusiva, processos vigentes e detecção de U nas análises CPRM."
- "Au: análises geoquímicas CPRM com Au > 0.1 ppm no Quadrilátero Ferrífero."
- "Nb: amostras de rocha com Nb detectado na região de Catalão/GO."
- "Li: análises de mineral/minério com lítio na Faixa Ribeira (MG/SP/RJ)."

### Agregados / Construção civil

- "Areia, brita, cascalho e saibro a 50 km de uma obra."
- "Pedreiras de granito ornamental no Espírito Santo."
- "Calcário para cimento na região metropolitana de Belo Horizonte."

### Metais base

- "Cobre no Pará: titulares e CFEM histórico."
- "Zinco em Vazante/MG."
- "Estanho (cassiterita) em Rondônia."
- "Manganês em Carajás."

### Energéticos

- "Carvão mineral em Santa Catarina."
- "Linhito e turfa: ainda há processos ativos?"
- "Tório: cruzamento com ocorrências CPRM."

### Industriais / Não-metálicos

- "Fosfato (rocha) no Triângulo Mineiro."
- "Caulim na Amazônia."
- "Talco, magnesita, vermiculita por UF."

### Gemas e ornamentais

- "Esmeraldas em Nova Era/MG."
- "Quartzo (cristal) no Tocantins."
- "Mármore na Bahia."

---

## 6. Cenários por Fase do Processo ANM

| Fase | Pergunta exemplo |
|------|------------------|
| Requerimento de Pesquisa | "Quantos requerimentos de pesquisa de lítio foram protocolados em 2025?" |
| Autorização de Pesquisa | "Autorizações vigentes de cobre em Goiás." |
| Requerimento de Lavra | "Processos de areia em requerimento de lavra na Grande SP." |
| Concessão de Lavra | "Concessões de ferro em operação no Quadrilátero Ferrífero." |
| Licenciamento (regime LMP) | "Processos sob licenciamento em Sergipe." |
| Permissão de Lavra Garimpeira | "PLGs vigentes no Tapajós/PA." |
| Registro de Extração | "Registros de extração para obras públicas em Rondônia." |
| Disponibilidade | "Áreas em disponibilidade publicadas pela ANM no último edital." |
| Indeferido / Extinto / Caduco | "Processos extintos de cassiterita em Rondônia desde 2020." |

---

## 7. Cenários de Workspace (Projeto, Memória, Histórico)

| Cenário | Pergunta exemplo |
|---------|------------------|
| Criar projeto | "Crie um projeto chamado 'Obra BR-381 km 480'." |
| Definir contexto de obra | "Use a coordenada -19.5, -44.0 como centro deste projeto." |
| Salvar fornecedor | "Adicione esses 5 fornecedores ao projeto atual." |
| Listar fornecedores salvos | "Quais fornecedores estão no meu projeto?" |
| Histórico de buscas | "Quais buscas eu fiz neste projeto na última semana?" |
| Comparar análises | "Compare a análise de areia que fiz ontem com a de hoje." |
| Favoritar processo | "Marque o processo 832.100/2017 como favorito." |
| Exportar relatório | "Gere um PDF com a análise consolidada deste projeto." |
| Memória de preferência | "Sempre que eu pedir 'concreteiras', ordene por capital social." |
| Follow-up implícito | "Sim, amplie o raio." (segue da última busca) |
| Limpar contexto | "Esqueça a obra atual e comece um novo projeto em Salvador." |

---

## 8. Cenários de Borda

### 8.1 Ambiguidade / Pedido vago

- "Mostra alguma coisa por aqui." → pedir clarificação.
- "Procuro mineração." → solicitar substância ou local.
- "Tem brita?" → solicitar local de referência.

### 8.2 Confusão entre raio (km) e tempo (isócrona)

- "Brita num raio de 100 km" → rota `hybrid` (não é isócrona).
- "Brita em até 100 minutos" → rota `hibrido_em_isocrona`.
- "Brita em 1h de caminhão" → rota `mineral_em_isocrona`/`hibrido_em_isocrona`.

### 8.3 Substância x produto industrial

- "Areia" → jazida (substância bruta).
- "Areia industrial para vidro" → híbrido (jazida + indústria).
- "Cimento" → empresa (industrializado, não jazida).
- "Cal hidratada" → empresa, mas com cruzamento para mina de calcário.

### 8.4 Substância semântica

- "Baterias de EV" → lítio + grafita + cobalto + níquel.
- "ETR / elementos do grupo das terras raras" → 17 elementos do grupo.
- "Minério estratégico" → lista pré-definida em
  `categoria_mineral_estrategica`.

### 8.5 Fora de escopo (recusa)

- Cotações financeiras intraday.
- Clima, notícias gerais, política.
- Receitas, esportes, entretenimento.
- Suporte de TI/programação.

### 8.6 Dados ausentes / falha de fonte

- "O CNPJ X não está no índice." → fallback BrasilAPI on-demand + cache 30 d.
- "Sem ocorrências CPRM nesse polígono." → resposta honesta + sugerir
  ampliação do raio.
- "Azure Maps indisponível" → degradar para `municipios_em_raio` baseado
  em distância euclidiana.

### 8.7 Erros de digitação / forma livre

- "lítion" → corrige para lítio.
- "carvao" → carvão mineral.
- "minas geras" → Minas Gerais.
- Número de processo sem barra ou com espaços (`871269 2024`,
  `871.269 2024`) → normalizar.

---

## 9. Checklist de Cobertura

Para validar uma versão do agente, garantir cobertura mínima em cada eixo:

- [ ] **8 rotas** do roteador testadas com ≥ 3 perguntas cada (24 testes).
- [ ] **Cada tool MCP** (19 jazidas + 9 empresas + 18 geo =
      **46 tools**) invocada ao menos uma vez por perguntas reais.
- [ ] **Cada índice OpenSearch** (mr_jazidas, mr_empresas, mr_cnae,
      mr_substancias, mr_cfem, mr_cprm, **mr_geoquimica**, mr_municipios,
      mr_biomas, mr_provincias, mr_sigef, mr_sicar, mr_mercado, mr_autuacoes,
      mr_monitoring) consumido ao menos uma vez.
- [ ] **Cada perfil de usuário** (8 perfis) com ao menos 3 perguntas
      end-to-end.
- [ ] **Edge cases**: ambiguidade, raio×isócrona, substância×industrial,
      fora de escopo, falha de fonte.
- [ ] **Follow-ups**: pelo menos 5 conversas multi-turno com herança de
      rota.
- [ ] **Workspace**: criar projeto, adicionar contexto, salvar resultado,
      exportar.
- [ ] **Idiomas / variações**: pelo menos 2 perguntas com erro de
      digitação, abreviações (UF), sinônimos.

---

*Documento mantido por: equipe MineralRadar. Sugestões de novos cenários
devem ser abertas como PR neste arquivo. Em caso de mudança nas rotas do
agente (`router.py`), atualizar este catálogo no mesmo commit.*
