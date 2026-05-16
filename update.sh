#!/usr/bin/env bash
# =============================================================================
#  update.sh — Atualiza o SaaS Farmácia após git pull
#  Uso: sudo bash update.sh
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[✓]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERRO]${NC}  $*" >&2; exit 1; }
step()    {
    echo
    echo -e "${BOLD}══════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  $*${NC}"
    echo -e "${BOLD}══════════════════════════════════════════════${NC}"
    echo
}

[[ $EUID -eq 0 ]] || error "Execute como root: sudo bash update.sh"

cd "${SCRIPT_DIR}"

# ── Garantir que o override de dev não interfira ──────────────────────────────
if [[ -f "docker-compose.override.yml" && ! -f "docker-compose.override.yml.bak" ]]; then
    mv docker-compose.override.yml docker-compose.override.yml.bak
    info "docker-compose.override.yml movido para .bak"
fi
export COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

# ── 1. Git pull ───────────────────────────────────────────────────────────────
step "1/4  Atualizando código"

if ! git -C "${SCRIPT_DIR}" rev-parse --is-inside-work-tree &>/dev/null; then
    error "Este diretório não é um repositório git. Faça o pull manualmente."
fi

BRANCH=$(git -C "${SCRIPT_DIR}" rev-parse --abbrev-ref HEAD)
info "Branch atual: ${BRANCH}"

git -C "${SCRIPT_DIR}" fetch origin
LOCAL=$(git -C "${SCRIPT_DIR}" rev-parse HEAD)
REMOTE=$(git -C "${SCRIPT_DIR}" rev-parse "origin/${BRANCH}")

if [[ "$LOCAL" == "$REMOTE" ]]; then
    warn "Já está atualizado (sem commits novos). Continuando mesmo assim..."
else
    COMMITS=$(git -C "${SCRIPT_DIR}" rev-list --count HEAD.."origin/${BRANCH}")
    info "Puxando ${COMMITS} commit(s) novos..."
    git -C "${SCRIPT_DIR}" pull origin "${BRANCH}"
    success "Código atualizado"
fi

# ── 2. Build apenas das imagens da aplicação ──────────────────────────────────
step "2/4  Fazendo build das imagens"

info "Rebuilding: api, worker, admin (infra não muda)"
docker compose build --pull api worker admin

success "Build concluído"

# ── 3. Restart com zero-downtime ──────────────────────────────────────────────
step "3/4  Aplicando atualização"

info "Subindo novos containers..."
docker compose up -d --no-deps api worker admin

info "Removendo imagens antigas não utilizadas..."
docker image prune -f &>/dev/null || true

success "Containers atualizados"

# ── 4. Health check ───────────────────────────────────────────────────────────
step "4/4  Verificando saúde"

info "Aguardando API ficar pronta..."
ATTEMPTS=0
while [[ $ATTEMPTS -lt 20 ]]; do
    if docker compose exec -T api curl -sf http://localhost:8000/health &>/dev/null; then
        break
    fi
    sleep 3
    ((ATTEMPTS++))
    printf "."
done
echo

if [[ $ATTEMPTS -ge 20 ]]; then
    warn "API ainda não responde após 60s. Verifique:"
    warn "  docker compose logs --tail=50 api"
else
    success "API respondendo normalmente"
fi

# ── Status final ──────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}Status dos containers:${NC}"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

echo
echo -e "${GREEN}${BOLD}Atualização concluída!${NC}"
echo -e "  Logs em tempo real:  ${CYAN}docker compose logs -f api${NC}"
echo -e "  Workers:             ${CYAN}docker compose logs -f worker${NC}"
echo
