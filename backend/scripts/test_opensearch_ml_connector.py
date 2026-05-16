"""
OpenSearch ML Connector — Azure OpenAI via AWS Secrets Manager
==============================================================

Fluxo:
    1. Cria o connector (SigV4, necessario para iam:PassRole)
    2. Registra um modelo externo usando o connector
    3. Faz deploy do modelo no cluster
    4. Testa com uma predicao (embedding)

Pre-requisitos:
    - AWS credentials configuradas: aws configure
      (o IAM principal precisa de iam:PassRole + es:ESHttp*)
    - IAM Role com trust policy para es.amazonaws.com
    - Secret no Secrets Manager com { "api_key": "<azure-openai-key>" }

Uso:
    cd backend
    python scripts/test_opensearch_ml_connector.py
"""

import asyncio
import json
import os
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Credenciais AWS ─────────────────────────────────────────────────────
# Defina via variáveis de ambiente antes de executar:
#   $env:AWS_ACCESS_KEY_ID="..."
#   $env:AWS_SECRET_ACCESS_KEY="..."
#   $env:AWS_DEFAULT_REGION="sa-east-1"
os.environ.setdefault("AWS_DEFAULT_REGION", "sa-east-1")

import boto3
import httpx
import requests
from requests_aws4auth import AWS4Auth
from mcp_servers.common.config import mcp_settings

# ── Constantes ────────────────────────────────────────────────────────────

OPENSEARCH_URL = mcp_settings.opensearch_endpoint.rstrip("/")
OS_USER        = mcp_settings.opensearch_user
OS_PASSWORD    = mcp_settings.opensearch_password
AWS_REGION     = "sa-east-1"

AZURE_ENDPOINT = mcp_settings.azure_openai_endpoint.rstrip("/")
AZURE_MODEL    = mcp_settings.embedding_model
AZURE_API_VER  = mcp_settings.azure_openai_embedding_api_version

ROLE_ARN   = "arn:aws:iam::880529453381:role/MineralRadar-OpenSearch-ML-Connector-Role"
SECRET_ARN = "arn:aws:secretsmanager:sa-east-1:880529453381:secret:supplyradar/opensearch-ml/azure-openai-c1i4Uh"

CONNECTOR_NAME = "azure-openai-embedding-v1"
MODEL_NAME     = "Azure OpenAI text-embedding (MineralRadar)"


# ── Auth helpers ──────────────────────────────────────────────────────────

def _build_auth():
    """
    SigV4 se houver credenciais AWS configuradas, basic auth como fallback.
    Com o Trust Policy correto em es.amazonaws.com, basic auth ja funciona
    para criar connectors (o OpenSearch service assume o role internamente).
    """
    try:
        session = boto3.Session(region_name=AWS_REGION)
        creds = session.get_credentials()
        if creds is None:
            raise RuntimeError("no creds")
        frozen = creds.get_frozen_credentials()
        if not frozen.access_key:
            raise RuntimeError("empty creds")
        auth = AWS4Auth(frozen.access_key, frozen.secret_key, AWS_REGION, "es",
                        session_token=frozen.token)
        print("  Auth     : SigV4 (AWS credentials encontradas)")
        return auth
    except Exception:
        print("  Auth     : Basic (sem credenciais AWS locais — usando admin/senha)")
        return (OS_USER, OS_PASSWORD)


_AUTH = None  # inicializado em main()


async def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Async OpenSearch REST call. Uses SigV4 or basic auth depending on _AUTH."""
    headers = {"Content-Type": "application/json"}
    content = json.dumps(body).encode() if body else None

    if isinstance(_AUTH, AWS4Auth):
        # SigV4: use requests (synchronous) in executor
        def _sync():
            resp = requests.request(
                method, f"{OPENSEARCH_URL}{path}",
                auth=_AUTH, headers=headers, data=content, verify=True, timeout=60,
            )
            try:
                return resp.status_code, resp.json()
            except Exception:
                return resp.status_code, {"raw": resp.text}

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)
    else:
        # Basic auth: use httpx async (handles non-ASCII passwords correctly)
        async with httpx.AsyncClient(
            base_url=OPENSEARCH_URL,
            auth=_AUTH,
            verify=True,
            timeout=60,
            headers=headers,
        ) as client:
            resp = await client.request(method, path, content=content)
            try:
                return resp.status_code, resp.json()
            except Exception:
                return resp.status_code, {"raw": resp.text}


# ── Steps ─────────────────────────────────────────────────────────────────

async def step1_create_connector() -> str:
    print("\n[1/4] Criando ML Connector (inline credential)...")

    body = {
        "name": CONNECTOR_NAME,
        "description": "Azure OpenAI Embedding connector - MineralRadar",
        "version": 1,
        "protocol": "http",
        "parameters": {
            "endpoint":    AZURE_ENDPOINT.replace("https://", ""),
            "model":       AZURE_MODEL,
            "api_version": AZURE_API_VER,
        },
        "credential": {
            "roleArn":   ROLE_ARN,
            "secretArn": SECRET_ARN,
        },
        "actions": [
            {
                "action_type": "predict",
                "method": "POST",
                "url": (
                    "https://${parameters.endpoint}/openai/deployments/"
                    "${parameters.model}/embeddings"
                    "?api-version=${parameters.api_version}"
                ),
                "headers": {"api-key": "${credential.api-key}"},
                "request_body": '{"input": ${parameters.input}}',
                "pre_process_function":  "connector.pre_process.openai.embedding",
                "post_process_function": "connector.post_process.openai.embedding",
            }
        ],
    }

    status, data = await call("POST", "/_plugins/_ml/connectors/_create", body)
    if status not in (200, 201) or "connector_id" not in data:
        print(f"  FALHOU ({status}):\n{json.dumps(data, indent=2)}")
        raise RuntimeError("Connector creation failed")

    cid = data["connector_id"]
    print(f"  OK  connector_id = {cid}")
    return cid


async def step2_register_model(connector_id: str) -> str:
    print("\n[2/4] Registrando modelo...")

    body = {
        "name": MODEL_NAME,
        "function_name": "remote",
        "description": "Azure OpenAI text-embedding via ML Commons",
        "connector_id": connector_id,
    }

    status, data = await call("POST", "/_plugins/_ml/models/_register", body)
    if status not in (200, 201):
        print(f"  FALHOU ({status}):\n{json.dumps(data, indent=2)}")
        raise RuntimeError("Model registration failed")

    if "task_id" in data:
        print(f"  ... assincrono, task_id={data['task_id']}, aguardando...")
        return await wait_for_task(data["task_id"])
    if "model_id" in data:
        model_id = data["model_id"]
        print(f"  OK  model_id = {model_id}")
        return model_id

    raise RuntimeError(f"Resposta inesperada: {data}")


async def step3_deploy_model(model_id: str) -> None:
    print("\n[3/4] Fazendo deploy do modelo...")

    status, data = await call("POST", f"/_plugins/_ml/models/{model_id}/_deploy")
    if status not in (200, 201):
        print(f"  FALHOU ({status}):\n{json.dumps(data, indent=2)}")
        raise RuntimeError("Model deploy failed")

    if "task_id" in data:
        print(f"  ... assincrono, task_id={data['task_id']}, aguardando...")
        await wait_for_task(data["task_id"])

    print("  OK  Modelo deployed")


async def step4_test_predict(model_id: str) -> None:
    print("\n[4/4] Testando predicao de embedding...")

    test_text = "minerio de ferro Minas Gerais"
    status, data = await call(
        "POST",
        f"/_plugins/_ml/models/{model_id}/_predict",
        {"parameters": {"input": [test_text]}},
    )

    if status != 200:
        print(f"  FALHOU ({status}):\n{json.dumps(data, indent=2)}")
        raise RuntimeError("Prediction failed")

    output = data.get("inference_results", [{}])[0]
    vector = output.get("output", [{}])[0].get("data", [])
    print(f"  OK  Embedding gerado!")
    print(f"      Texto     : '{test_text}'")
    print(f"      Dimensoes : {len(vector)}")
    print(f"      [:5]      : {vector[:5]}")


# ── Poll task ─────────────────────────────────────────────────────────────

async def wait_for_task(task_id: str, max_wait: int = 120) -> str:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        status, data = await call("GET", f"/_plugins/_ml/tasks/{task_id}")
        state    = data.get("state", "")
        model_id = data.get("model_id")

        if state == "COMPLETED":
            if not model_id:
                raise RuntimeError(f"COMPLETED sem model_id: {data}")
            print(f"  OK  model_id = {model_id}")
            return model_id
        if state == "FAILED":
            raise RuntimeError(f"Task FAILED: {data.get('error', data)}")

        print(f"      state={state} ...")
        await asyncio.sleep(3)

    raise TimeoutError(f"Task {task_id} nao concluiu em {max_wait}s")


# ── Main ──────────────────────────────────────────────────────────────────

async def main():
    global _AUTH

    print("=" * 62)
    print("OpenSearch ML Connector - Azure OpenAI via Secrets Manager")
    print("=" * 62)
    print(f"  Cluster  : {OPENSEARCH_URL}")
    print(f"  Modelo   : {AZURE_MODEL}")
    print(f"  Role     : {ROLE_ARN.split('/')[-1]}")
    print(f"  Secret   : {SECRET_ARN.split(':')[-1]}")

    _AUTH = _build_auth()

    # Verifica conectividade com OpenSearch
    status, info = await call("GET", "/")
    version = info.get("version", {}).get("number", "?")
    if status != 200:
        print(f"  Cluster inacessivel ({status}): {info}")
        sys.exit(1)
    print(f"  OpenSearch   : {version}")

    connector_id = await step1_create_connector()
    model_id     = await step2_register_model(connector_id)
    await step3_deploy_model(model_id)
    await step4_test_predict(model_id)

    print("\n" + "=" * 62)
    print("SUCESSO - Conector funcionando!")
    print(f"  connector_id = {connector_id}")
    print(f"  model_id     = {model_id}")
    print("\nAdicione ao .env:")
    print(f"  OPENSEARCH_ML_CONNECTOR_ID={connector_id}")
    print(f"  OPENSEARCH_ML_MODEL_ID={model_id}")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
