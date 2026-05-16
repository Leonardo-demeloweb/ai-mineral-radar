#!/usr/bin/env bash
# =============================================================================
# MineralRadar 2.0 — MCP Servers Launcher
# =============================================================================
#
# Levanta os 3 MCP servers em background com logs separados.
#
# Uso:
#   ./scripts/start_mcp.sh          # inicia os 3 servers
#   ./scripts/start_mcp.sh --stop   # para todos os servers
#   ./scripts/start_mcp.sh --status # mostra status (up/down)
#   ./scripts/start_mcp.sh --logs   # tail dos 3 logs simultaneamente
#
# Logs:   /tmp/mcp_jazidas.log | /tmp/mcp_empresas.log | /tmp/mcp_geo.log
# PIDs:   /tmp/.mcp_servers.pids
# =============================================================================

set -euo pipefail

# ── Configuração ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

JAZIDAS_MODULE="mcp_servers.jazidas.server:app"
EMPRESAS_MODULE="mcp_servers.empresas.server:app"
GEO_MODULE="mcp_servers.geo.server:app"

JAZIDAS_PORT=8110
EMPRESAS_PORT=8111
GEO_PORT=8112

LOG_JAZIDAS="/tmp/mcp_jazidas.log"
LOG_EMPRESAS="/tmp/mcp_empresas.log"
LOG_GEO="/tmp/mcp_geo.log"
PID_FILE="/tmp/.mcp_servers.pids"

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Helpers ────────────────────────────────────────────────────────────────────

log()    { echo -e "${BOLD}[MCP]${RESET} $*"; }
ok()     { echo -e "${GREEN}  ✔${RESET} $*"; }
warn()   { echo -e "${YELLOW}  ⚠${RESET} $*"; }
fail()   { echo -e "${RED}  ✘${RESET} $*"; }

is_port_open() {
    local port=$1
    lsof -i :"$port" -sTCP:LISTEN -t &>/dev/null
}

wait_for_port() {
    local name=$1
    local port=$2
    local timeout=15
    local elapsed=0
    while ! is_port_open "$port"; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            fail "$name não subiu na porta $port em ${timeout}s — veja o log"
            return 1
        fi
    done
    ok "$name UP em :$port"
}

# ── Subcomandos ────────────────────────────────────────────────────────────────

cmd_stop() {
    log "Parando MCP servers..."
    if [[ ! -f "$PID_FILE" ]]; then
        warn "Arquivo de PIDs não encontrado ($PID_FILE). Nenhum server registrado."
        return 0
    fi

    while IFS='=' read -r name pid; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            ok "$name (PID $pid) encerrado"
        else
            warn "$name (PID $pid) já estava parado"
        fi
    done < "$PID_FILE"

    rm -f "$PID_FILE"
    log "Pronto."
}

cmd_status() {
    local servers=("jazidas:$JAZIDAS_PORT" "empresas:$EMPRESAS_PORT" "geo:$GEO_PORT")
    echo ""
    echo -e "${BOLD}MCP Servers — Status${RESET}"
    echo "────────────────────────────────"
    for entry in "${servers[@]}"; do
        local name="${entry%%:*}"
        local port="${entry##*:}"
        if is_port_open "$port"; then
            echo -e "  ${GREEN}●${RESET} $name   :$port   UP"
        else
            echo -e "  ${RED}●${RESET} $name   :$port   DOWN"
        fi
    done
    echo ""
}

cmd_logs() {
    log "Abrindo logs (Ctrl+C para sair)..."
    tail -f "$LOG_JAZIDAS" "$LOG_EMPRESAS" "$LOG_GEO"
}

cmd_start() {
    echo ""
    echo -e "${CYAN}${BOLD}MineralRadar 2.0 — MCP Servers${RESET}"
    echo "══════════════════════════════════════"
    log "Backend dir: $BACKEND_DIR"
    echo ""

    # Garante que estamos no diretório correto (PYTHONPATH)
    cd "$BACKEND_DIR"

    # Detecta o Python/uvicorn — prioridade: venv do projeto → anaconda → PATH
    if [[ -f "venv/bin/uvicorn" ]]; then
        UVICORN="venv/bin/uvicorn"
    elif [[ -f ".venv/bin/uvicorn" ]]; then
        UVICORN=".venv/bin/uvicorn"
    elif [[ -f "/opt/anaconda3/bin/uvicorn" ]]; then
        UVICORN="/opt/anaconda3/bin/uvicorn"
    elif command -v uvicorn &>/dev/null; then
        UVICORN="uvicorn"
    else
        fail "uvicorn não encontrado. Instale: pip install uvicorn"
        exit 1
    fi
    log "Usando: $UVICORN"

    # Para servers existentes antes de reiniciar
    if [[ -f "$PID_FILE" ]]; then
        warn "Servers já registrados — parando antes de reiniciar..."
        cmd_stop
        sleep 1
    fi

    # Verifica se portas já estão em uso por outro processo
    for port in $JAZIDAS_PORT $EMPRESAS_PORT $GEO_PORT; do
        if is_port_open "$port"; then
            fail "Porta $port já está em uso por outro processo."
            fail "Rode: lsof -i :$port"
            exit 1
        fi
    done

    log "Iniciando servers em background..."
    echo ""

    # ── Jazidas :8110 ──────────────────────────────────────────────────────
    "$UVICORN" "$JAZIDAS_MODULE" \
        --host 0.0.0.0 \
        --port "$JAZIDAS_PORT" \
        --log-level info \
        > "$LOG_JAZIDAS" 2>&1 &
    PID_JAZIDAS=$!
    echo "jazidas=$PID_JAZIDAS" >> "$PID_FILE"

    # ── Empresas :8111 ─────────────────────────────────────────────────────
    "$UVICORN" "$EMPRESAS_MODULE" \
        --host 0.0.0.0 \
        --port "$EMPRESAS_PORT" \
        --log-level info \
        > "$LOG_EMPRESAS" 2>&1 &
    PID_EMPRESAS=$!
    echo "empresas=$PID_EMPRESAS" >> "$PID_FILE"

    # ── Geo :8112 ──────────────────────────────────────────────────────────
    "$UVICORN" "$GEO_MODULE" \
        --host 0.0.0.0 \
        --port "$GEO_PORT" \
        --log-level info \
        > "$LOG_GEO" 2>&1 &
    PID_GEO=$!
    echo "geo=$PID_GEO" >> "$PID_FILE"

    log "Aguardando servers subirem..."
    echo ""

    wait_for_port "jazidas " "$JAZIDAS_PORT"
    wait_for_port "empresas" "$EMPRESAS_PORT"
    wait_for_port "geo     " "$GEO_PORT"

    echo ""
    echo "────────────────────────────────────────────"
    echo -e "${GREEN}${BOLD}  Todos os MCP servers estão UP!${RESET}"
    echo "────────────────────────────────────────────"
    echo ""
    echo -e "  ${CYAN}Jazidas ${RESET}  http://localhost:$JAZIDAS_PORT/mcp   (PID $PID_JAZIDAS)"
    echo -e "  ${CYAN}Empresas${RESET}  http://localhost:$EMPRESAS_PORT/mcp   (PID $PID_EMPRESAS)"
    echo -e "  ${CYAN}Geo     ${RESET}  http://localhost:$GEO_PORT/mcp   (PID $PID_GEO)"
    echo ""
    echo -e "  ${YELLOW}Logs:${RESET}"
    echo -e "    tail -f $LOG_JAZIDAS"
    echo -e "    tail -f $LOG_EMPRESAS"
    echo -e "    tail -f $LOG_GEO"
    echo ""
    echo -e "  ${YELLOW}Para parar:${RESET}  ./scripts/start_mcp.sh --stop"
    echo -e "  ${YELLOW}Ver logs:  ${RESET}  ./scripts/start_mcp.sh --logs"
    echo ""
}

# ── Entry point ────────────────────────────────────────────────────────────────

case "${1:-}" in
    --stop)   cmd_stop   ;;
    --status) cmd_status ;;
    --logs)   cmd_logs   ;;
    "")       cmd_start  ;;
    *)
        echo "Uso: $0 [--stop | --status | --logs]"
        exit 1
        ;;
esac
