# Bases de Dados — MineralRadar

**Documento gerado em:** 05 de maio de 2026  
**Última atualização:** 12 de maio de 2026 (índice `mr_geoquimica_v001` — CPRM Geoquímica rocha + mineral/minério via OGC API + bot `bot_geoquimica.py`)  
**Contexto:** Mapeamento de todas as fontes de dados públicas e privadas necessárias para o MineralRadar — plataforma independente de inteligência mineral estratégica, terras raras e processos minerários no Brasil.

---

## Legenda de status


| Símbolo                     | Significado                                                                   |
| --------------------------- | ----------------------------------------------------------------------------- |
| ✅ **Confirmado disponível** | Fonte verificada e acessível; ETL será construído para o cluster MineralRadar |
| 🟡 **ETL necessário**       | Fonte pública disponível, mas precisa de ingestão/indexação                   |
| 🔵 **Integração API**       | Fonte pública com API direta, conexão em tempo real viável                    |
| 🔴 **Pago / Privado**       | Requer contrato ou licença comercial                                          |
| ⚪ **Complexo / Parceria**   | Requer scraping, parceria com órgão ou acesso restrito                        |


---

## BLOCO 1 — Fontes Brasileiras Públicas (Domínio Regulatório e Minerário)

### 1.1 ANM — Agência Nacional de Mineração

**É a espinha dorsal do sistema. Todo o ETL será construído do zero para o cluster MineralRadar.**

> ⚠️ **Atenção — mudança de endpoint (verificado em 05/05/2026):**  
> A URL `app.anm.gov.br/dadosabertos/SIGMINE/PROCESSOS_MINERARIOS/` retorna **404**. A ANM migrou o endereço dos dados abertos (há notícia oficial publicada em `gov.br/anm`).
>
> **Sobre o ArcGIS REST da ANM (`geo.anm.gov.br`):**  
> Este serviço é a **camada de visualização de mapas** da ANM — útil apenas para renderizar tiles no frontend. **Não deve ser usado como fonte de ETL** por razões técnicas objetivas: paginação limitada (~2.000 registros/request), sem garantia de completude para carga bulk, latência alta, sem suporte a download diferencial confiável. Para 25M+ documentos seria inviável.
>
> **Fontes corretas para o ETL do MineralRadar:**
>
> - **Cadastro Mineiro (tabular):** arquivos de texto/CSV disponíveis em `dados.gov.br` → conjuntos ANM. Atualização diária. Contém todas as tabelas relacionais dos processos (processos, substâncias, municípios, fases, titulares, eventos).
> - **SIGMINE (shapes):** arquivos ZIP com Shapefiles disponíveis via portal de dados abertos da ANM (`gov.br/anm/pt-br/acesso-a-informacao/dados-abertos`). O novo endereço do download bulk **precisa ser confirmado** — o endpoint antigo (`app.anm.gov.br/dadosabertos/SIGMINE/`) está offline.
> - **CFEM, RAL, SICOP:** CSV em `dados.gov.br` — endereços estáveis, não foram afetados pela migração.


| Base                                        | Conteúdo                                                                                       | Formato / Acesso                                                                                                                                                           | Status                        | Prioridade  |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- | ----------- |
| **SIGMINE — Processos ativos**              | Polígonos, fases, substâncias, titulares, datas. ~600K processos ativos                        | ZIP com Shapefiles. `https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/{UF}.zip` ou `BRASIL.zip` (~123MB). Atualizado diariamente.                              | 🟡 ETL necessário             | **Crítica** |
| **SIGMINE — Processos inativos**            | Histórico completo de processos encerrados. ~24M docs históricos                               | `https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/PROCESSOS_INATIVOS.zip` (~150MB). O ETL indexará ativos e inativos juntos — campo `ativo: bool` para filtro. | 🟡 ETL necessário             | Alta        |
| **Cadastro Mineiro (SICOP)**                | Dados administrativos dos processos: requerimentos, portarias, atos, prazos, obrigações legais | CSV diário em `dados.gov.br` → buscar "SICOP ANM"                                                                                                                          | 🟡 ETL novo                   | Alta        |
| **CFEM — Compensação Financeira**           | Arrecadação por substância, empresa e município. Série histórica desde 2010.                   | CSV diário em `dados.gov.br`                                                                                                                                               | 🟡 ETL novo                   | Alta        |
| **RAL — Relatório Anual de Lavra**          | Produção bruta, beneficiada e água mineral por empresa, substância, município                  | CSV em `dados.gov.br` (base AMB)                                                                                                                                           | 🟡 ETL novo                   | Média       |
| **DOU — Diário Oficial da União**           | Publicações de atos minerários: portarias, autorizações, cancelamentos, recursos               | API IN DOU (`in.gov.br/leituradou`)                                                                                                                                        | 🔵 API em tempo real          | Alta        |
| **SEI — Sistema Eletrônico de Informações** | Andamento processual interno da ANM: despachos, ofícios, pareceres técnicos                    | Consulta web pública (pesquisa avançada por processo)                                                                                                                      | ⚪ Scraping controlado         | Média       |
| **Anuário Mineral Brasileiro (AMB)**        | Estatísticas de produção, exportação, importação, CFEM por substância. Série desde 2010.       | CSV/Excel anual em `dados.gov.br`                                                                                                                                          | 🟡 ETL enriquecimento         | Média       |
| **Sumário Mineral / Informe Mineral**       | Análises trimestrais/anuais do desempenho de substâncias no mercado interno                    | PDF + planilhas no portal ANM                                                                                                                                              | 🟡 ETL / processamento de PDF | Baixa       |
| **Áreas em Disponibilidade (Leilões ANM)**  | Áreas cujo processo foi extinto e estão disponíveis para novo requerimento                     | Editais publicados no DOU + tabelas no portal ANM                                                                                                                          | 🟡 ETL novo                   | Alta        |
| **Observatório da CFEM**                    | Dashboard interativo de arrecadação por localidade, período e empresa                          | API interna do Observatório (scraping viável)                                                                                                                              | ⚪ Scraping                    | Baixa       |


---

### 1.2 CPRM / SGB — Serviço Geológico do Brasil

**Fundamentais para o contexto geológico que o MineralRadar v2 não tem.**


| Base                                       | Conteúdo                                                                                                    | Formato / Acesso                                                                                          | Status            | Prioridade  |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ----------------- | ----------- |
| **Ocorrências Minerais (GeoBank)**         | Localização, substância, tipo de depósito, teor, referências bibliográficas. Banco nacional de ocorrências. | WMS + WFS + REST GeoJSON. URL: `geoportal.sgb.gov.br/server/rest/services/geologia/ocorrencias/MapServer` | 🔵 API disponível | **Crítica** |
| **Mapa Geológico do Brasil (escala 1:1M)** | Unidades litoestratigráficas, idades geológicas, províncias, estruturas                                     | WMS/WFS ArcGIS. URL: `geoportal.sgb.gov.br` + OGC-API: `geoservicos.sgb.gov.br/ogcapi`                    | 🔵 API disponível | Alta        |
| **Afloramentos Geológicos**                | Pontos de campo com tipo de rocha, fotos, estruturas                                                        | WMS + WFS. URL: `geoportal.sgb.gov.br/server/rest/services/geologia/afloramentos/MapServer`               | 🔵 API disponível | Média       |
| **Geoquímica (rocha + mineral/minério)**   | Amostras de campo com teores por analito (Ag, Au, Cu, Nb, terras raras, etc.), método, projeto, laboratório | **OGC API Features:** `geoservicos.sgb.gov.br/ogcapi/collections/geologia/geoquimica/` — coleções `analises-rocha` (~61K) e `analises-mineral-minerio` (~4K) · Índice: **`mr_geoquimica_v001`** · ETL: `mineral-radar-etl/bots/bot_geoquimica.py` | 🔵 API disponível | Alta       |
| **Províncias Minerais**                    | Delimitação das províncias minerais brasileiras (Carajás, Quadrilátero Ferrífero, Borborema, etc.)          | WMS GeoPortal SGB                                                                                         | 🔵 API disponível | Alta        |
| **SIAGAS — Hidrogeologia**                 | Poços tubulares, aquíferos, dados hidrogeológicos                                                           | Portal SIAGAS com API REST                                                                                | 🔵 API disponível | Baixa       |
| **Furos de Sondagem (GEOTERM)**            | Dados de sondagens geotérmicas e projetos históricos da CPRM                                                | Consulta no portal SGB                                                                                    | ⚪ Complexo        | Baixa       |


---

### 1.3 IBGE — Instituto Brasileiro de Geografia e Estatística

**Fonte pública estável. ETL via API IBGE.**


| Base                           | Conteúdo                                                                                            | Formato / Acesso           | Status              | Prioridade |
| ------------------------------ | --------------------------------------------------------------------------------------------------- | -------------------------- | ------------------- | ---------- |
| **Municípios + polígonos**     | Limites, centroides, códigos cruzados                                                               | API IBGE + WFS             | 🟡 ETL necessário   | Alta       |
| **Biomas**                     | Delimitação dos 6 biomas brasileiros (Amazônia, Cerrado, Caatinga, Mata Atlântica, Pantanal, Pampa) | Download/WFS `ibge.gov.br` | 🟡 ETL novo         | Média      |
| **Malha de estados e regiões** | Polígonos estaduais e regionais                                                                     | API IBGE                   | 🟡 ETL complementar | Baixa      |


---

### 1.4 MMA / IBAMA — Meio Ambiente

**Essenciais para verificar restrições ambientais sobre áreas de interesse.**


| Base                                       | Conteúdo                                                                          | Formato / Acesso                                                                                                                             | Status                | Prioridade  |
| ------------------------------------------ | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- | ----------- |
| **CNUC — Unidades de Conservação**         | Polígonos de UCs federais, estaduais e municipais (APA, REBIO, ESEC, PARNA, etc.) | ArcGIS REST JSON/GeoJSON. URL: `pamgia.ibama.gov.br/server/rest/services/SIGAGEO/bases/MapServer/19`. Shapefile mensal em `dados.mma.gov.br` | 🔵 API disponível     | **Crítica** |
| **CAR / SICAR — Cadastro Ambiental Rural** | Polígonos de imóveis rurais com reserva legal e APP declaradas                    | API pública + download bulk                                                                                                                  | 🟡 ETL novo           | Alta        |
| **IBAMA — Autuações e Embargos**           | Histórico de infrações ambientais por empresa/CNPJ/localização                    | CSV em `dados.gov.br`                                                                                                                        | 🟡 ETL novo           | Alta        |
| **Licenças Ambientais (SISLAM/SISLIC)**    | Licenças LP, LI, LO por empreendimento (federal)                                  | Consulta pública IBAMA                                                                                                                       | ⚪ Scraping / parceria | Média       |


---

### 1.5 FUNAI — Fundação Nacional dos Povos Indígenas


| Base                 | Conteúdo                                                                               | Formato / Acesso                                                   | Status      | Prioridade  |
| -------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | ----------- | ----------- |
| **Terras Indígenas** | Polígonos de TIs em diferentes estágios (homologada, declarada, em estudo, delimitada) | Shapefile/KML/GeoJSON mensal. GeoServer FUNAI. URL: `gov.br/funai` | 🟡 ETL novo | **Crítica** |


> **Por que crítico:** A Constituição Federal proíbe mineração em TIs sem lei complementar. Qualquer processo que sobreponha uma TI está em situação legal complexa. É o principal risco jurídico de um projeto mineral.

---

### 1.6 INCRA — Instituto Nacional de Colonização e Reforma Agrária


| Base                       | Conteúdo                                                                   | Formato / Acesso                 | Status      | Prioridade |
| -------------------------- | -------------------------------------------------------------------------- | -------------------------------- | ----------- | ---------- |
| **SIGEF — Imóveis rurais** | Polígonos de propriedades rurais certificadas pelo INCRA                   | WFS público `sigef.incra.gov.br` | 🟡 ETL novo | Média      |
| **Assentamentos**          | Áreas de assentamentos rurais (implicam direitos superficiários complexos) | Download/WFS INCRA               | 🟡 ETL novo | Baixa      |


---

### 1.7 Comércio Exterior — MDIC


| Base                                      | Conteúdo                                                                                                                                        | Formato / Acesso                                                    | Status            | Prioridade |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ----------------- | ---------- |
| **ComexStat — Exportações e Importações** | Dados mensais por NCM (código de produto), país, UF, valor FOB/CIF, peso. Série desde 1997. Permite filtrar por NCM de minerais e terras raras. | API REST documentada: `api-comexstat.mdic.gov.br` + CSV bulk mensal | 🔵 API disponível | Alta       |


> NCMs relevantes para terras raras: **2805.30** (terras raras, Sc, Y), **2846.xx** (compostos de TR), **8505.11** (ímãs permanentes), **3825** (resíduos de TR).

---

### 1.8 Receita Federal do Brasil


| Base                            | Conteúdo                                                                         | Formato / Acesso                                                | Status                             | Prioridade |
| ------------------------------- | -------------------------------------------------------------------------------- | --------------------------------------------------------------- | ---------------------------------- | ---------- |
| **CNPJ — Cadastro de Empresas** | Razão social, CNAE, capital social, sócios, situação cadastral, endereço, porte  | Download bulk mensal `dados.rfb.gov.br` (~40GB, 221M registros) | 🟡 ETL necessário (com pré-filtro) | Alta       |
| **CNAE**                        | Classificação nacional de atividades econômicas com descrições e embeddings k-NN | Tabela CNAE pública `cnae.ibge.gov.br`                          | 🟡 ETL necessário                  | Média      |


> **Estratégia de filtragem RFB — crítica para custo do cluster**
>
> O bulk completo da RFB é ~~221M estabelecimentos / 68 GB. **Indexar tudo seria desnecessário** — o MineralRadar só precisa de empresas relacionadas ao domínio mineral. Aplicamos um **pré-filtro de 4 critérios** que reduz o índice para ~350K CNPJs (~~400 MB):
>
> 1. **Titulares ANM** — CNPJ-básico (8 dígitos) que aparece em qualquer processo SIGMINE
> 2. **CNAE Indústrias Extrativas** — códigos 05xx a 09xx (carvão, petróleo, minerais metálicos e não-metálicos, atividades de apoio)
> 3. **Maiores arrecadadores CFEM** — top CNPJs por arrecadação histórica
> 4. **Sócios PJ recursivos (1 nível)** — para análise de controle societário das empresas dos critérios 1–3
>
> **Fallback on-demand:** quando o agente busca um CNPJ não indexado (ex: novo titular ANM antes do refresh mensal RFB), o `bot_indexador.py` faz lookup via [BrasilAPI](https://brasilapi.com.br/api/cnpj/v1/) e indexa sob demanda, com cache no Redis por 30 dias.
>
> **Holdings estrangeiras** (controladoras de junior miners listadas em TSX/ASX) **não estão na RFB**. Tratamento via CVM (B3) e OpenCorporates — fora do índice principal `rfb_cnpj_v001`.
>
> Ver `SPEC_ETL_MINERALRADAR.md` §10 para implementação detalhada.

---

### 1.9 Outras fontes públicas de suporte


| Base                                    | Conteúdo                                                                                  | Acesso                        | Status            | Prioridade |
| --------------------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------- | ----------------- | ---------- |
| **ANTT — Malha rodoviária**             | Rodovias federais e estaduais com extensão e estado de conservação                        | WFS/download                  | 🟡 ETL            | Baixa      |
| **ANTAQ — Portos e hidrovias**          | Dados de portos fluviais/marítimos organizados                                            | CSV público ANTAQ             | 🟡 ETL necessário | Baixa      |
| **ENEVA / ONS — Energia**               | Linhas de transmissão, subestações (relevante para projetos de mineração de grande porte) | WMS ONS                       | ⚪ Complexo        | Baixa      |
| **CAPES / CNPq — Pesquisas acadêmicas** | Publicações, teses e projetos de pesquisa em geologia e mineração                         | API CAPES/Sucupira (limitada) | ⚪ Complexo        | Baixa      |


---

## BLOCO 2 — Fontes Privadas e Comerciais

### 2.1 Dados de Mercado e Preços de Minerais


| Fonte                                                        | Conteúdo                                                                                                                                            | Modelo                                                     | Prioridade          |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ------------------- |
| **Metals-API** (`metals-api.com`)                            | Preços em tempo real e históricos de terras raras (Nd, Pr, Dy, Tb, Ce, La, etc.), lítio, nióbio, cobalto. API JSON com endpoints de série temporal. | Freemium — plano pago para histórico e TR                  | Alta                |
| **Benchmark Mineral Intelligence** (`benchmarkminerals.com`) | Avaliações mensais de preços de TR (óxidos e metais), padrão IOSCO, usados por montadoras e governos. O mais respeitado do setor.                   | Assinatura premium (~USD 5k–20k/ano)                       | Média (longo prazo) |
| **Argus Media** (`argusmedia.com`)                           | +70 avaliações de preços de TR, previsões de 10 anos por substância, análise oferta/demanda.                                                        | Assinatura premium                                         | Média (longo prazo) |
| **LME — London Metal Exchange**                              | Preços de metais base (Co, Ni, Cu, Li — via índices). TR não são negociados diretamente no LME.                                                     | API pública para metais base; TR requer assinatura         | Média               |
| **Shanghai Metal Market (SMM)**                              | Preços do mercado chinês de TR, metais de bateria, ímãs de NdFeB. China controla ~90% da produção de TR.                                            | Assinatura (dados críticos para entender o mercado global) | Média               |


---

### 2.2 Inteligência Mineral e Geológica


| Fonte                                           | Conteúdo                                                                                                                                                                 | Modelo                                   | Prioridade            |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------- | --------------------- |
| **S&P Global — SNL Metals & Mining**            | Banco global de projetos minerais: recursos/reservas (JORC/CRIRSCO), CAPEX, OPEX, histórico de M&A, dados de empresas listadas. O maior banco de dados privado do setor. | Licença enterprise (~USD 50k+/ano)       | Baixa (fase avançada) |
| **Wood Mackenzie** (`woodmac.com`)              | Análise de mercado, previsões de oferta/demanda por mineral, análise de projetos, M&A.                                                                                   | Licença enterprise                       | Baixa (fase avançada) |
| **Roskill (agora parte da Wood Mackenzie)**     | Relatórios de mercado por commodities específicas (TR, Li, Nb, grafita). Histórico sólido em TR.                                                                         | Relatórios pontuais (~USD 5k–15k cada)   | Média                 |
| **USGS — Mineral Resources Data System (MRDS)** | Base global de depósitos minerais com tipologia, substâncias, localização. Pública dos EUA, cobre o Brasil.                                                              | Download gratuito em `mrdata.usgs.gov`   | Alta — **gratuito**   |
| **Critical Minerals Mapping Initiative (CMMI)** | Dados colaborativos de depósitos de minerais críticos globais                                                                                                            | Download gratuito (parceria USGS/BGS/GA) | Alta — **gratuito**   |


---

### 2.3 Inteligência Empresarial e Societária


| Fonte                                     | Conteúdo                                                                                                             | Modelo                                       | Prioridade           |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | -------------------- |
| **B3 — Bolsa Brasileira**                 | Empresas listadas no setor de mineração, fatos relevantes, ITR/DFP (balanços), composição acionária                  | API B3 (parcialmente pública) + scraping CVM | 🔵/⚪ Médio           |
| **CVM — Companhias Abertas (cadastro)**   | Cadastro de todas as companhias abertas: setor, situação, tipo de mercado (BOLSA/BALCÃO), datas, auditor. Filtrado para universo mineral + cross-ref CNPJ jazidas/empresas | `dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv` (atualizado diariamente) · Índice: **`mr_cvm_listadas_v001`** · ETL: `mineral-radar-etl/bots/bot_cvm.py` · Tool MCP: `buscar_empresa_cvm` | ✅ **Implementado** |
| **OpenCorporates**                        | Dados societários de empresas em múltiplos países (útil para controladoras estrangeiras de projetos de TR no Brasil) | API freemium                                 | Média                |
| **Jusbrasil / Escavador**                 | Processos judiciais, certidões negativas, ações trabalhistas por empresa/CNPJ                                        | Assinatura                                   | Alta (due diligence) |


---

### 2.4 Dados de Exploração e Sensoriamento Remoto


| Fonte                               | Conteúdo                                                                                       | Modelo                            | Prioridade        |
| ----------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------- | ----------------- |
| **INPE — Catálogo de Imagens**      | Imagens Landsat, CBERS, Sentinel para a área do projeto                                        | Gratuito — `catalogo.dgi.inpe.br` | 🔵 API disponível |
| **Copernicus (ESA) — Sentinel-2**   | Imagens multiespectrais de alta resolução, úteis para mapeamento de alteração hidrotermal      | API ESA gratuita                  | 🔵 API disponível |
| **SRTM / ALOS — Dados de Elevação** | Modelos digitais de elevação (topografia, análise de bacia drenagem)                           | Download USGS/JAXA gratuito       | 🟡 ETL            |
| **ASTER — Dados Espectrais**        | Imagens térmicas e SWIR para mapeamento de argilominerais e óxidos de ferro (exploração de TR) | NASA earthdata — gratuito         | 🟡 ETL            |


---

### 2.5 Notícias e Monitoramento de Reputação


| Fonte                | Conteúdo                                                                                                                                   | Modelo                                  | Prioridade |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------- | ---------- |
| **Tavily AI Search** | Busca web semântica para notícias sobre empresas, projetos minerais, licenças. Já especificado no `SPEC_ENRIQUECIMENTO_WEB.md` do sistema. | API paga (já planejada no MineralRadar) | Alta       |
| **Brave Search API** | Índice web alternativo, útil para notícias recentes de mineradoras e projetos                                                              | API paga (já planejada no MineralRadar) | Alta       |
| **GDELT Project**    | Base global de notícias georreferenciadas. Útil para monitorar conflitos, protestos e eventos ligados a projetos minerais                  | Gratuito — BigQuery                     | Média      |


---

## BLOCO 3 — Visão consolidada por módulo do MineralRadar

### Módulo 1 — Mapa de Processos e Exploração

> *"Onde posso prospectar? O que há nessa área?"*


| Fonte necessária                      | Status            | Esforço de integração                                   |
| ------------------------------------- | ----------------- | ------------------------------------------------------- |
| ANM SIGMINE (ativos + inativos)       | 🟡 ETL necessário | `bot_anm.py` → PostGIS → OpenSearch (ativos + inativos) |
| CPRM — Ocorrências minerais (GeoBank) | 🔵 API disponível | Médio — ETL + índice **`mr_cprm_v001`** (OGC API `recursos-minerais`) |
| CPRM — Geoquímica (rocha + mineral)   | 🔵 API disponível | Médio — índice **`mr_geoquimica_v001`** · `bot_geoquimica.py` (OGC API, sem passo obrigatório por PostGIS) |
| CPRM — Mapa geológico                 | 🔵 API            | Médio — tiles WMS no frontend                           |
| ANM — Áreas em disponibilidade        | 🟡 ETL            | Médio                                                   |
| USGS MRDS                             | Gratuito          | Médio — ETL novo                                        |


---

### Módulo 2 — Due Diligence e Análise de Processo

> *"Esse processo é sólido? Quem é o titular? Quais são os riscos?"*


| Fonte necessária                                 | Status            | Esforço de integração                     |
| ------------------------------------------------ | ----------------- | ----------------------------------------- |
| ANM SIGMINE (detalhes, fases, eventos, títulos)  | 🟡 ETL necessário | Incluído no `bot_anm.py` + SCM microdados |
| ANM Cadastro Mineiro (prazos, obrigações, SICOP) | 🟡 ETL            | Alto                                      |
| ANM CFEM histórico                               | 🟡 ETL            | Médio                                     |
| ANM RAL (produção declarada)                     | 🟡 ETL            | Médio                                     |
| RFB CNPJ (titular, sócios)                       | 🟡 ETL necessário | `bot_rfb.py` — bulk mensal                |
| CVM (se empresa listada)                         | 🔵 API gratuita   | Médio                                     |
| IBAMA — Autuações                                | 🟡 ETL            | Médio                                     |
| Jusbrasil/Escavador                              | 🔴 Pago           | Alto                                      |


---

### Módulo 3 — Restrições e Sobreposições

> *"Essa área tem impedimentos legais ou ambientais?"*


| Fonte necessária                       | Status | Esforço de integração   |
| -------------------------------------- | ------ | ----------------------- |
| FUNAI — Terras Indígenas               | 🟡 ETL | Baixo — download mensal |
| IBAMA — Unidades de Conservação (CNUC) | 🔵 API | Baixo                   |
| MMA — CAR/SICAR (propriedades rurais)  | 🟡 ETL | Médio                   |
| IBGE — Biomas                          | 🟡 ETL | Baixo                   |
| INCRA — SIGEF                          | 🟡 ETL | Médio                   |


---

### Módulo 4 — Monitoramento Contínuo

> *"O que mudou nos meus processos hoje?"*


| Fonte necessária                    | Status     | Esforço de integração            |
| ----------------------------------- | ---------- | -------------------------------- |
| ANM DOU (via API IN)                | 🔵 API     | Alto — parser de atos minerários |
| ANM SEI (movimentações)             | ⚪ Scraping | Alto                             |
| ANM Cadastro Mineiro (prazos SICOP) | 🟡 ETL     | Alto                             |
| IBAMA — Novas autuações             | 🟡 ETL     | Médio                            |


---

### Módulo 5 — Inteligência de Mercado (Minerais Estratégicos e Terras Raras)

> *"Qual é o valor desta jazida? Onde esse mineral é consumido no mundo?"*


| Fonte necessária                                 | Status               | Esforço de integração              |
| ------------------------------------------------ | -------------------- | ---------------------------------- |
| Metals-API (preços TR em tempo real)             | 🔴 Pago              | Baixo — REST API simples           |
| ComexStat MDIC (exportações/importações por NCM) | 🔵 API gratuita      | Médio                              |
| ANM AMB/Sumário Mineral (produção nacional)      | 🟡 ETL               | Baixo                              |
| USGS Mineral Commodity Summaries                 | Gratuito (PDF anual) | Médio — processamento de documento |
| Benchmark / Argus (análise profunda)             | 🔴 Premium           | Alto — fase avançada               |


---

### Módulo 6 — Logística Mineral

> *"Como escoar o minério? Custo de frete, porto mais próximo, ferrovia?"*


| Fonte necessária                          | Status            | Esforço de integração     |
| ----------------------------------------- | ----------------- | ------------------------- |
| Azure Maps (rotas, isócronas)             | 🔵 API disponível | Integração direta via SDK |
| ANTAQ — Portos                            | 🟡 ETL necessário | CSV público ANTAQ         |
| ANTT — Rodovias e ferrovias               | 🟡 ETL            | Médio                     |
| DNit — Estado de conservação das rodovias | 🔵 API (parcial)  | Médio                     |


---

## BLOCO 4 — Resumo executivo de prioridades

### Fase 1 — Base mínima funcional (0–3 meses)

*O que dá para fazer com pouco esforço e alto impacto:*

1. **ETL ANM SIGMINE (ativos + inativos)** — `bot_anm.py` com download de `dadosabertos.anm.gov.br`. Indexar campo `ativo: bool` para permitir análise histórica dos ~24M processos inativos desde o início.
2. **Classificador de Minerais Estratégicos** — Módulo Python puro, sem ETL adicional, aplicado durante a indexação ANM. Classifica as 862 substâncias ANM em categorias estratégicas (TR, Li, Nb, Co, grafita, urânio). Alto impacto, baixo custo.
3. **CPRM Ocorrências Minerais (GeoBank)** — OGC API `geoservicos.sgb.gov.br` (coleção `recursos-minerais`). Índice OpenSearch **`mr_cprm_v001`** (`bot_cprm.py`). **CPRM Geoquímica** — mesma API, coleções `analises-rocha` + `analises-mineral-minerio`; índice **`mr_geoquimica_v001`** (`bot_geoquimica.py`). Ambos conectam prospectividade e teores analíticos ao agente.
4. **FUNAI Terras Indígenas** — Download mensal (GeoJSON/Shapefile), ETL simples. Remove o maior risco jurídico invisível do sistema atual.
5. **IBAMA CNUC** — API GeoJSON disponível. ETL simples. Sobreposição com Unidades de Conservação.
6. **ANM CFEM** — CSV diário em `dados.gov.br`. Indica se o processo tem produção declarada real (vs. processo "na gaveta").
7. **Metals-API** — Preços de TR em tempo real. API REST simples, custo baixo.

### Fase 2 — Plataforma completa (3–9 meses)

1. ANM Cadastro Mineiro / SICOP (prazos e obrigações)
2. CPRM Mapa Geológico (WMS tiles no frontend) + geoquímica indexada (`mr_geoquimica_v001`) quando aplicável
3. ComexStat MDIC (exportações/importações por NCM mineral)
4. CVM (dados de mineradoras listadas)
5. CAR/SICAR (propriedades rurais que sobrepõem processos)
6. Enriquecimento web (Tavily + Brave — já especificado no sistema)
7. USGS MRDS (depósitos minerais globais — gratuito, ETL único)

### Fase 3 — Diferencial competitivo (9–18 meses)

1. ANM DOU/SEI (monitoramento legal em tempo real)
2. IBAMA autuações e licenças
3. Sensoriamento remoto (Sentinel-2 para mapeamento espectral)
4. Benchmark Mineral Intelligence / Argus (dados de mercado premium)
5. S&P Global SNL (banco global de projetos — para due diligence institucional)

---

## BLOCO 5 — Observações técnicas de integração

### Sobre os dados ANM e a estratégia de indexação

> **Verificado em 05/05/2026:**

**Volume da ANM:** O portal `dadosabertos.anm.gov.br` disponibiliza ~600K processos ativos (BRASIL.zip, ~123MB) e ~24M processos históricos inativos (PROCESSOS_INATIVOS.zip, ~150MB). O ETL do MineralRadar indexará **ambos** desde a Fase 1, com campo `ativo: bool` para filtro padrão nas queries.

**Estratégia de indexação:** O índice `anm_processos_v001` do MineralRadar incluirá desde o início os campos enriquecidos: `categoria_mineral_estrategica`, `cfem_total_historico`, `restricoes_geo` (pré-computado no PostGIS), `ativo`. Isso é superior a qualquer sistema que indexe apenas dados brutos do Shapefile.

**Inativos são escopo explícito** — análise de reativação de minas estratégicas e estudo histórico de processos encerrados são casos de uso prioritários do MineralRadar.

### Padrão de referência para o ETL do MineralRadar

O padrão de 3 bots em sequência é a arquitetura comprovada para ETL geoespacial de grande volume:

```
ANM — ZIPs com Shapefiles + CSVs tabulares
         │
         ▼
    [ bot_anm.py ]  →  PostgreSQL + PostGIS  (staging + sobreposições)
         │
    [ bot_cfem.py, bot_rfb.py, bot_funai.py... ]  →  PostgreSQL (join multi-fonte)
         │
         ▼
    [ bot_indexador.py ]  →  OpenSearch MineralRadar
                              (ativos e inativos, enriquecidos)
```

**Princípios do ETL MineralRadar:**

- Inativos indexados desde o início — campo `ativo: bool` para filtro nas queries
- Sobreposições geo pré-computadas no PostGIS antes de indexar
- Hash diferencial — só reindexar documentos alterados
- Cluster próprio e isolado — sem dependência de infraestrutura externa
- **RFB filtrado por relevância mineral** — apenas ~350K CNPJs vs. 221M do bulk completo

### Sizing do cluster OpenSearch — Fase 1 e 2


| Índice                                            | Volume          | Tamanho estimado |
| ------------------------------------------------- | --------------- | ---------------- |
| `anm_processos_v001` (ativos + inativos)          | ~25M docs       | ~6 GB            |
| `rfb_cnpj_v001` (filtrado por relevância mineral) | ~350K docs      | ~400 MB          |
| `anm_substancia_v001` (com embeddings k-NN)       | 862 docs        | ~5 MB            |
| `ibge_municipio_v001` (com geo_shape)             | 5.631 docs      | ~950 MB          |
| `rfb_cnae_v001` (com embeddings k-NN)             | 2.394 docs      | ~20 MB           |
| `mr_cprm_v001` (Fase 2)                           | ~36K docs       | ~25 MB           |
| `mr_geoquimica_v001` (Fase 2)                     | ~65K docs       | ~80–120 MB       |
| `restricoes_geo_v001` (Fase 2)                    | ~100K polígonos | ~2 GB            |
| **Total estimado**                                | **~25,5M docs** | **~10 GB**       |


**Implicação prática:** com ~10 GB total, o cluster cabe confortavelmente em:

- **Oracle Cloud Free Tier** — 2 VMs ARM (4 OCPU + 24 GB RAM total) — gratuito
- **AWS `t3.small.search`** com 30 GB EBS — ~US$ 25/mês (12 meses no Free Tier)
- **Self-hosted Hetzner** — VPS 16 GB RAM — ~EUR 16/mês

Sem o pré-filtro RFB (i.e. indexando os 221M registros completos), o cluster precisaria de ~~80 GB e instâncias `r6g.xlarge` (~~US$ 600/mês).

**Sobre as fontes de dados para o ETL:**  
O MineralRadar baixará arquivos bulk (ZIPs de Shapefiles para SIGMINE, CSVs para CFEM/SCM/SICOP) diretamente de `dadosabertos.anm.gov.br`. Esta é a abordagem correta — não APIs de mapa (ArcGIS REST).

O ArcGIS REST da ANM (`geo.anm.gov.br`) é a **camada de visualização de mapas**, não uma fonte de ETL. É tecnicamente inviável para 25M+ documentos: paginação limitada a ~2.000 registros/request, sem download diferencial confiável, sem garantia de completude dos campos internos. Seu uso no projeto se restringe a **renderização de tiles WMS no frontend**.

**Fontes corretas para o ETL (downloads bulk):**


| Dado                                    | Canal                                    | Obs.                                           |
| --------------------------------------- | ---------------------------------------- | ---------------------------------------------- |
| Cadastro Mineiro (tabular)              | `dados.gov.br` → "Cadastro Mineiro ANM"  | Estável, atualização diária                    |
| SIGMINE shapes (ZIPs ativos + inativos) | Portal dados abertos ANM                 | **Novo endereço a confirmar** — antigo offline |
| CFEM                                    | `dados.gov.br` → "CFEM ANM"              | Estável                                        |
| RAL                                     | `dados.gov.br` → "Anuário Mineral / RAL" | Estável                                        |
| SICOP                                   | `dados.gov.br` → "SICOP ANM"             | Estável                                        |


**O ETL do MineralRadar** é construído do zero com PostgreSQL + PostGIS como staging. Ver `SPEC_ETL_MINERALRADAR.md` para arquitetura detalhada.

### Sobre resolução de substâncias de terras raras

O índice `anm_substancia_v001` do MineralRadar indexará as 862 substâncias cadastradas na ANM com campo `categoria_estrategica` (terra_rara / litio / niobio / cobalto / grafita / uranio / outro) e embeddings k-NN gerados no `bot_indexador.py`. As 17 terras raras (La, Ce, Pr, Nd, Pm, Sm, Eu, Gd, Tb, Dy, Ho, Er, Tm, Yb, Lu + Sc + Y) terão tratamento prioritário na classificação.

### Sobre a geometria de sobreposições

O sistema já usa `geo_shape` no OpenSearch para polígonos de processos ANM. A mesma estrutura suporta sobreposições com TIs, UCs, biomas e SICAR — todos disponíveis como GeoJSON/Shapefile. A query OpenSearch `geo_shape / relation: intersects` já está implementada no codebase (`jazidas_por_poligono`). O esforço é apenas de ETL dos novos índices.

### Sobre o ComexStat

Os NCMs de minerais estratégicos relevantes para o produto:


| Substância                        | NCMs principais          |
| --------------------------------- | ------------------------ |
| Terras raras (óxidos, carbonatos) | 2805.30, 2846.10–2846.90 |
| Nióbio (columbita, ferronióbio)   | 2615.90, 7202.93         |
| Lítio (espodumênio, carbonato)    | 2825.20, 2530.20         |
| Grafita natural                   | 2504.10, 2504.90         |
| Cobalto                           | 2605.00, 8105.20         |
| Urânio                            | 2612.10, 2844.xx         |
| Titânio (ilmenita, rutilo)        | 2614.00                  |
| Manganês                          | 2602.00                  |
| Ferro (minério)                   | 2601.11, 2601.12         |


---

*Documento gerado como base de planejamento técnico. URLs de APIs verificadas em maio de 2026.*