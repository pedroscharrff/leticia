#!/usr/bin/env bash
# =============================================================================
#  deploy.sh — Deploy automatizado completo para SaaS Farmácia
#  Sistema: Ubuntu 22.04 LTS (ou Debian 12)
#  Execução: sudo bash deploy.sh
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/deploy.log"

# ── Cores ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[✓]${NC}    $*" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*" | tee -a "$LOG_FILE"; }
error()   { echo -e "${RED}[ERRO]${NC}  $*" | tee -a "$LOG_FILE" >&2; exit 1; }
step()    {
    echo | tee -a "$LOG_FILE"
    echo -e "${BOLD}${BLUE}══════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD}${BLUE}  $*${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD}${BLUE}══════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo | tee -a "$LOG_FILE"
}

# ── Banner ────────────────────────────────────────────────────────────────────
banner() {
    clear
    echo -e "${BOLD}${CYAN}"
    cat << 'EOF'
  ╔══════════════════════════════════════════════════╗
  ║       SaaS Farmácia — Deploy Automatizado        ║
  ║   WhatsApp AI Multi-Tenant para Farmácias        ║
  ╚══════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
    echo -e "  Log em tempo real: ${YELLOW}${LOG_FILE}${NC}"
    echo
}

# ── Verificar root ────────────────────────────────────────────────────────────
check_root() {
    [[ $EUID -eq 0 ]] || error "Execute como root: sudo bash deploy.sh"
}

# ── Verificar estrutura de diretórios ─────────────────────────────────────────
check_structure() {
    step "Verificando estrutura do projeto"

    local required_dirs=("agents" "llm" "api" "frontend" "nginx")
    local required_files=("docker-compose.yml" "api/Dockerfile" "frontend/Dockerfile")
    local missing=()

    for dir in "${required_dirs[@]}"; do
        if [[ ! -d "${SCRIPT_DIR}/${dir}" ]]; then
            missing+=("DIRETÓRIO: ${dir}/")
        fi
    done

    for file in "${required_files[@]}"; do
        if [[ ! -f "${SCRIPT_DIR}/${file}" ]]; then
            missing+=("ARQUIVO: ${file}")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo -e "${RED}Estrutura do projeto incompleta!${NC}"
        echo -e "  Diretório de trabalho: ${SCRIPT_DIR}"
        echo
        echo -e "  ${YELLOW}Itens faltando:${NC}"
        for item in "${missing[@]}"; do
            echo -e "    ✗ ${item}"
        done
        echo
        echo -e "  ${BOLD}Estrutura esperada:${NC}"
        cat << 'TREE'
  /caminho/do/projeto/          ← execute deploy.sh daqui
  ├── agents/                   ← lógica dos agentes LangGraph
  ├── llm/
  ├── api/
  │   ├── Dockerfile
  │   └── requirements.txt
  ├── frontend/
  │   └── Dockerfile
  ├── nginx/
  ├── docker-compose.yml
  └── deploy.sh                 ← este script
TREE
        echo
        echo -e "  ${YELLOW}Se seu projeto está em subpasta (ex: saas-farmacia/), execute:${NC}"
        echo -e "    cd saas-farmacia && sudo bash deploy.sh"
        echo
        error "Corrija a estrutura antes de continuar."
    fi

    # Verifica se agents/ tem conteúdo
    local agents_files
    agents_files=$(find "${SCRIPT_DIR}/agents" -name "*.py" 2>/dev/null | wc -l)
    if [[ "$agents_files" -eq 0 ]]; then
        error "O diretório agents/ existe mas está vazio. Verifique o upload do projeto."
    fi

    success "Estrutura do projeto OK (${agents_files} arquivos Python em agents/)"
}

# ── Instalar dependências do sistema ──────────────────────────────────────────
install_system_deps() {
    step "Instalando dependências do sistema"
    apt-get update -qq 2>>"$LOG_FILE"
    apt-get install -y -qq \
        curl wget git openssl ufw fail2ban \
        dnsutils ca-certificates gnupg lsb-release \
        2>>"$LOG_FILE"
    success "Dependências instaladas"
}

# ── Instalar Docker ───────────────────────────────────────────────────────────
install_docker() {
    step "Verificando Docker"

    if command -v docker &>/dev/null; then
        success "Docker já instalado: $(docker --version)"
        return
    fi

    info "Instalando Docker Engine..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>>"$LOG_FILE"
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
        tee /etc/apt/sources.list.d/docker.list >/dev/null

    apt-get update -qq 2>>"$LOG_FILE"
    apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin \
        2>>"$LOG_FILE"

    systemctl enable --now docker 2>>"$LOG_FILE"
    success "Docker instalado: $(docker --version)"
}

# ── Configurar Firewall ───────────────────────────────────────────────────────
configure_firewall() {
    step "Configurando Firewall (UFW)"

    ufw --force reset >>"$LOG_FILE" 2>&1
    ufw default deny incoming  >>"$LOG_FILE" 2>&1
    ufw default allow outgoing >>"$LOG_FILE" 2>&1
    ufw allow 22/tcp   comment 'SSH'   >>"$LOG_FILE" 2>&1
    ufw allow 80/tcp   comment 'HTTP'  >>"$LOG_FILE" 2>&1
    ufw allow 443/tcp  comment 'HTTPS' >>"$LOG_FILE" 2>&1
    ufw --force enable >>"$LOG_FILE" 2>&1

    success "Firewall ativo — portas abertas: 22 (SSH), 80 (HTTP), 443 (HTTPS)"
    info "  Grafana (3000), RabbitMQ (15672) acessíveis via SSH tunnel apenas"
}

# ── Coletar configurações ─────────────────────────────────────────────────────
collect_config() {
    step "Configuração da Aplicação"
    echo -e "  ${YELLOW}Preencha os dados abaixo. Senhas serão geradas automaticamente.${NC}"
    echo

    # Domínio
    while true; do
        read -rp "  Domínio principal (ex: farmacia.io): " DOMAIN
        DOMAIN="${DOMAIN,,}"  # lowercase
        [[ "$DOMAIN" =~ ^[a-z0-9][a-z0-9.-]+\.[a-z]{2,}$ ]] && break
        warn "  Domínio inválido. Tente novamente."
    done
    API_DOMAIN="api.${DOMAIN}"
    ADMIN_DOMAIN="admin.${DOMAIN}"

    # E-mail SSL
    while true; do
        read -rp "  E-mail para certificado SSL (Let's Encrypt): " SSL_EMAIL
        [[ "$SSL_EMAIL" == *@*.* ]] && break
        warn "  E-mail inválido."
    done

    # API Keys
    echo
    while true; do
        read -rp "  ANTHROPIC_API_KEY (sk-ant-...): " ANTHROPIC_API_KEY
        [[ -n "$ANTHROPIC_API_KEY" ]] && break
        warn "  API Key não pode ser vazia."
    done

    read -rp "  GOOGLE_API_KEY (Enter para pular): " GOOGLE_API_KEY
    GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"

    # Credenciais admin
    echo
    read -rp "  E-mail do admin do painel [admin@${DOMAIN}]: " ADMIN_EMAIL
    ADMIN_EMAIL="${ADMIN_EMAIL:-admin@${DOMAIN}}"

    while true; do
        read -rsp "  Senha do admin (mín. 12 chars): " ADMIN_PASSWORD; echo
        [[ ${#ADMIN_PASSWORD} -ge 12 ]] && break
        warn "  Senha muito curta."
    done

    while true; do
        read -rsp "  Confirme a senha do admin: " ADMIN_PASSWORD_CONFIRM; echo
        [[ "$ADMIN_PASSWORD" == "$ADMIN_PASSWORD_CONFIRM" ]] && break
        warn "  Senhas não coincidem."
    done

    read -rsp "  Senha do Grafana [gerada automaticamente]: " GRAFANA_PASSWORD; echo
    GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-$(openssl rand -hex 12)}"

    # Auto-gerar segredos
    DB_PASSWORD=$(openssl rand -hex 32)
    SECRET_KEY=$(openssl rand -hex 64)
    RABBITMQ_PASS=$(openssl rand -hex 16)

    # Resumo
    echo
    echo -e "  ${BOLD}Resumo da configuração:${NC}"
    echo -e "  ┌──────────────────────────────────────────────────┐"
    echo -e "  │  API:         ${CYAN}https://${API_DOMAIN}${NC}"
    echo -e "  │  Admin:       ${CYAN}https://${ADMIN_DOMAIN}${NC}"
    echo -e "  │  Admin email: ${CYAN}${ADMIN_EMAIL}${NC}"
    echo -e "  │  SSL e-mail:  ${CYAN}${SSL_EMAIL}${NC}"
    echo -e "  └──────────────────────────────────────────────────┘"
    echo
    read -rp "  Confirmar e iniciar deploy? [s/N]: " confirm
    [[ "${confirm,,}" == "s" ]] || error "Deploy cancelado pelo usuário."
}

# ── Verificar DNS ─────────────────────────────────────────────────────────────
check_dns() {
    step "Verificando apontamento de DNS"

    local server_ip
    server_ip=$(curl -s --max-time 5 ifconfig.me \
             || curl -s --max-time 5 api.ipify.org \
             || echo "")

    info "IP deste servidor: ${server_ip:-desconhecido}"

    local all_ok=true

    for fqdn in "${API_DOMAIN}" "${ADMIN_DOMAIN}"; do
        local resolved
        resolved=$(dig +short "${fqdn}" 2>/dev/null | grep -E '^[0-9]+\.' | tail -1 || echo "")

        if [[ -n "$resolved" && "$resolved" == "$server_ip" ]]; then
            success "${fqdn} → ${resolved} ✓"
        else
            warn "${fqdn} → ${resolved:-não encontrado} (esperado: ${server_ip})"
            all_ok=false
        fi
    done

    if [[ "$all_ok" == false ]]; then
        echo
        warn "DNS ainda não propagado para um ou mais domínios."
        echo -e "  Configure no painel do seu registrador:"
        echo
        echo -e "    ${BOLD}A   api.${DOMAIN}    →  ${server_ip}${NC}"
        echo -e "    ${BOLD}A   admin.${DOMAIN}  →  ${server_ip}${NC}"
        echo
        warn "A propagação pode levar até 48h (geralmente < 5 min na Cloudflare)."
        echo
        read -rp "  Tentar emitir o certificado mesmo assim? [s/N]: " force_ssl
        [[ "${force_ssl,,}" == "s" ]] || error "Configure o DNS e execute novamente."
    fi
}

# ── Gerar hash da senha do admin ──────────────────────────────────────────────
hash_admin_password() {
    step "Gerando hash da senha do admin"
    info "Iniciando container Python para bcrypt..."

    # Escapa aspas simples na senha para uso seguro no shell
    local safe_pass
    safe_pass=$(printf '%s' "$ADMIN_PASSWORD" | sed "s/'/'\\\\''/g")

    ADMIN_PASSWORD_HASH=$(docker run --rm python:3.12-slim sh -c \
        "pip install bcrypt --quiet --quiet 2>/dev/null && \
         python -c \"import bcrypt; print(bcrypt.hashpw(b'${safe_pass}', bcrypt.gensalt(12)).decode())\"")

    [[ -n "$ADMIN_PASSWORD_HASH" ]] || error "Falha ao gerar hash da senha."
    success "Hash bcrypt gerado"
}

# ── Gerar arquivo .env ────────────────────────────────────────────────────────
write_env() {
    step "Gerando arquivo .env"

    # O hash bcrypt contém '$2b$12$...' — docker-compose interpreta '$' como variável.
    # Escapamos '$' → '$$' no .env para que docker-compose passe o valor correto ao container.
    local escaped_hash
    escaped_hash="${ADMIN_PASSWORD_HASH//\$/\$\$}"

    cat > "${SCRIPT_DIR}/.env" << EOF
# ─── Gerado por deploy.sh em $(date) ─────────────────────────────────────────
# ⚠  NUNCA faça commit deste arquivo!

# ─── Força uso apenas do docker-compose.yml (ignora override de dev) ─────────
COMPOSE_FILE=docker-compose.yml

# ─── Database ────────────────────────────────────────────────────────────────
DB_PASSWORD=${DB_PASSWORD}

# ─── RabbitMQ ────────────────────────────────────────────────────────────────
RABBITMQ_PASS=${RABBITMQ_PASS}

# ─── LLM API Keys ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
GOOGLE_API_KEY=${GOOGLE_API_KEY}

# ─── App ─────────────────────────────────────────────────────────────────────
SECRET_KEY=${SECRET_KEY}
ENVIRONMENT=production
LOG_LEVEL=INFO

# ─── Admin ───────────────────────────────────────────────────────────────────
ADMIN_EMAIL=${ADMIN_EMAIL}
ADMIN_PASSWORD_HASH=${escaped_hash}

# ─── CORS ────────────────────────────────────────────────────────────────────
CORS_ORIGINS=https://${ADMIN_DOMAIN},https://${API_DOMAIN}

# ─── Limites ─────────────────────────────────────────────────────────────────
CELERY_WORKERS_CONCURRENCY=16
SESSION_TTL_SECONDS=1800
MAX_CONTEXT_MESSAGES=10
ANALYST_MAX_RETRIES=1
LLM_TIMEOUT_SECONDS=30

# ─── Grafana ─────────────────────────────────────────────────────────────────
GRAFANA_PASSWORD=${GRAFANA_PASSWORD}
EOF

    chmod 600 "${SCRIPT_DIR}/.env"
    success ".env gerado com permissões 600"
}

# ── Nginx: config HTTP temporária (para emissão do certificado) ───────────────
write_nginx_http() {
    mkdir -p "${SCRIPT_DIR}/nginx"
    cat > "${SCRIPT_DIR}/nginx/default.conf" << EOF
# ─── Configuração temporária HTTP — emissão do certificado Let's Encrypt ──────
server {
    listen 80;
    server_name ${API_DOMAIN} ${ADMIN_DOMAIN};

    # ACME challenge (Let's Encrypt)
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files \$uri =404;
    }

    location / {
        return 200 'Aguardando SSL...';
        add_header Content-Type text/plain;
    }
}
EOF
    info "Configuração HTTP temporária do nginx gerada"
}

# ── Nginx: config HTTPS definitiva ────────────────────────────────────────────
write_nginx_https() {
    cat > "${SCRIPT_DIR}/nginx/default.conf" << EOF
# ─── Gerado por deploy.sh em $(date) ─────────────────────────────────────────
# Domínios: ${API_DOMAIN}  |  ${ADMIN_DOMAIN}

# Rate limiting zones
limit_req_zone \$binary_remote_addr zone=webhook:10m  rate=30r/m;
limit_req_zone \$binary_remote_addr zone=api_pub:10m  rate=120r/m;
limit_req_zone \$binary_remote_addr zone=api_auth:10m rate=10r/m;

# ── HTTP → HTTPS redirect + ACME renewal ─────────────────────────────────────
server {
    listen 80;
    server_name ${API_DOMAIN} ${ADMIN_DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files \$uri =404;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

# ── API — https://${API_DOMAIN} ───────────────────────────────────────────────
server {
    listen 443 ssl http2;
    server_name ${API_DOMAIN};

    # Certificado SSL (cobre api.${DOMAIN} e admin.${DOMAIN})
    ssl_certificate     /etc/letsencrypt/live/${API_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${API_DOMAIN}/privkey.pem;

    # Parâmetros SSL modernos
    ssl_protocols             TLSv1.2 TLSv1.3;
    ssl_ciphers               ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    ssl_session_cache         shared:SSL:10m;
    ssl_session_timeout       1d;
    ssl_stapling              on;
    ssl_stapling_verify       on;

    # Security headers
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options           DENY                                            always;
    add_header X-Content-Type-Options    nosniff                                         always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin"               always;
    add_header X-XSS-Protection          "1; mode=block"                                 always;

    # Tamanho máximo do body (mensagens WhatsApp com mídia)
    client_max_body_size 10M;

    # ── Webhook — recebe mensagens das plataformas WhatsApp ───────────────────
    # URL: POST https://${API_DOMAIN}/webhook/{tenant_id}
    # Header: X-API-Key: <api_key_do_tenant>
    location /webhook/ {
        limit_req zone=webhook burst=20 nodelay;

        proxy_pass            http://api:8000;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
        proxy_read_timeout    60s;
        proxy_connect_timeout 10s;
        proxy_send_timeout    30s;
    }

    # ── Auth endpoints ────────────────────────────────────────────────────────
    location /auth/ {
        limit_req zone=api_auth burst=5 nodelay;

        proxy_pass            http://api:8000;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
    }

    # ── Health check (sem rate limit — para load balancers) ──────────────────
    location /health {
        proxy_pass         http://api:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host \$host;
        access_log         off;
    }

    # ── Docs da API (Swagger / ReDoc) ─────────────────────────────────────────
    location ~ ^/(docs|redoc|openapi\.json)$ {
        proxy_pass         http://api:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host \$host;
    }

    # ── Admin API (protegido por autenticação na própria API) ─────────────────
    location /admin/ {
        limit_req zone=api_pub burst=30 nodelay;

        proxy_pass            http://api:8000;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
    }

    # ── API geral ─────────────────────────────────────────────────────────────
    location / {
        limit_req zone=api_pub burst=30 nodelay;

        proxy_pass            http://api:8000;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
        proxy_read_timeout    60s;
    }
}

# ── Painel Admin — https://${ADMIN_DOMAIN} ────────────────────────────────────
server {
    listen 443 ssl http2;
    server_name ${ADMIN_DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${API_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${API_DOMAIN}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options           DENY                                            always;
    add_header X-Content-Type-Options    nosniff                                         always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin"               always;
    # CSP permite chamadas para a API
    add_header Content-Security-Policy   "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https://${API_DOMAIN}; font-src 'self';" always;

    # Gzip
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;
    gzip_min_length 1024;

    # SPA React Admin
    location / {
        proxy_pass            http://admin:80;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
        proxy_read_timeout    30s;
    }
}
EOF
    success "Configuração HTTPS do nginx gerada"
}

# ── Emitir certificado SSL ────────────────────────────────────────────────────
issue_ssl() {
    step "Emitindo Certificado SSL (Let's Encrypt)"

    # Sobe APENAS nginx (--no-deps) para servir o challenge ACME.
    # Não construímos api/admin aqui — apenas nginx precisa estar online.
    info "Iniciando nginx em modo HTTP para validação ACME..."
    docker compose up -d --no-deps nginx 2>>"$LOG_FILE"
    sleep 4

    # Verifica se nginx subiu
    docker compose ps nginx | grep -q "Up" \
        || error "nginx não subiu. Veja: docker compose logs nginx"

    # Emite o certificado via certbot (webroot).
    # --entrypoint certbot sobrescreve o loop de renovação definido no compose,
    # garantindo que o container execute 'certonly' em vez de travar no loop.
    info "Solicitando certificado para: ${API_DOMAIN}, ${ADMIN_DOMAIN}"
    docker compose run --rm --entrypoint certbot certbot certonly \
        --webroot \
        --webroot-path=/var/www/certbot \
        --email "${SSL_EMAIL}" \
        --agree-tos \
        --no-eff-email \
        --force-renewal \
        -d "${API_DOMAIN}" \
        -d "${ADMIN_DOMAIN}" \
        2>>"$LOG_FILE" \
        || error "Falha ao emitir certificado. Verifique se o DNS está apontado corretamente e as portas 80/443 estão abertas."

    success "Certificado SSL emitido com sucesso!"

    # Troca para config HTTPS e recarrega nginx
    info "Ativando configuração HTTPS no nginx..."
    write_nginx_https
    docker compose exec nginx nginx -s reload 2>>"$LOG_FILE" \
        || docker compose restart nginx 2>>"$LOG_FILE"
    sleep 2

    success "nginx rodando com HTTPS/TLS 1.3"
}

# ── Iniciar todos os serviços ─────────────────────────────────────────────────
start_services() {
    step "Iniciando Todos os Serviços"

    info "Fazendo build das imagens Docker..."
    docker compose build --no-cache 2>>"$LOG_FILE"

    info "Subindo todos os serviços..."
    docker compose up -d 2>>"$LOG_FILE"

    info "Aguardando serviços ficarem saudáveis..."
    local attempts=0
    local healthy=false

    while [[ $attempts -lt 40 ]]; do
        if docker compose ps | grep -E "(api|postgres|redis|rabbitmq)" | grep -q "healthy"; then
            healthy=true
            break
        fi
        sleep 5
        ((attempts++))
        printf "."
    done
    echo

    if [[ "$healthy" == true ]]; then
        success "Todos os serviços saudáveis"
    else
        warn "Alguns serviços ainda estão iniciando. Verifique com: docker compose ps"
    fi
}

# ── Verificação de saúde ──────────────────────────────────────────────────────
health_check() {
    step "Verificação de Saúde da API"

    local url="https://${API_DOMAIN}/health"
    info "Testando: ${url}"

    local attempts=0
    while [[ $attempts -lt 15 ]]; do
        if curl -sf --max-time 5 "${url}" &>/dev/null; then
            success "API respondendo em ${url}"
            return
        fi
        sleep 4
        ((attempts++))
        printf "."
    done
    echo

    warn "API ainda não responde via HTTPS. Pode estar iniciando ainda."
    warn "Verifique com: docker compose logs -f api"
}

# ── Salvar credenciais ────────────────────────────────────────────────────────
save_credentials() {
    local file="${SCRIPT_DIR}/CREDENCIAIS.txt"

    cat > "$file" << EOF
╔══════════════════════════════════════════════════════════════════════╗
║            SaaS Farmácia — Credenciais de Acesso                     ║
║            Gerado em: $(date)
╚══════════════════════════════════════════════════════════════════════╝

🌐  URLs de Acesso
──────────────────────────────────────────────────────────────────────
  Webhook (API):     https://${API_DOMAIN}/webhook/{tenant_id}
  Painel Admin:      https://${ADMIN_DOMAIN}
  Swagger Docs:      https://${API_DOMAIN}/docs
  Health Check:      https://${API_DOMAIN}/health

🔐  Admin do Painel
──────────────────────────────────────────────────────────────────────
  E-mail:    ${ADMIN_EMAIL}
  Senha:     ${ADMIN_PASSWORD}

📊  Monitoramento — acessar via SSH Tunnel
──────────────────────────────────────────────────────────────────────
  Comando:    ssh -L 3000:localhost:3000 -L 15672:localhost:15672 user@${server_ip:-SERVER_IP}
  Grafana:    http://localhost:3000    (admin / ${GRAFANA_PASSWORD})
  RabbitMQ:   http://localhost:15672  (farmacia / ${RABBITMQ_PASS})
  Prometheus: http://localhost:9090

🔗  Integração com Gateway WhatsApp (WAHA / Uazapi / Evolution API)
──────────────────────────────────────────────────────────────────────
  Método: POST
  URL:    https://${API_DOMAIN}/webhook/{TENANT_ID}
  Header: X-API-Key: {API_KEY_DO_TENANT}
  Body:   { "phone": "5511999999999", "message": "Olá" }

🛠  Comandos Úteis
──────────────────────────────────────────────────────────────────────
  Ver logs da API:       docker compose logs -f api
  Ver status:            docker compose ps
  Atualizar aplicação:   git pull && docker compose up -d --build
  Reiniciar serviço:     docker compose restart api
  Renovar SSL manual:    docker compose run --rm certbot renew

⚠   GUARDE ESTE ARQUIVO COM SEGURANÇA!
    Apague-o do servidor após copiar as senhas para um gerenciador seguro.
EOF

    chmod 600 "$file"
    success "Credenciais salvas em CREDENCIAIS.txt (chmod 600)"
}

# ── Resumo final ──────────────────────────────────────────────────────────────
print_summary() {
    local server_ip
    server_ip=$(curl -s --max-time 5 ifconfig.me || echo "")

    echo
    echo -e "${GREEN}${BOLD}"
    cat << 'EOF'
  ╔══════════════════════════════════════════════════════╗
  ║            ✅  DEPLOY CONCLUÍDO COM SUCESSO!         ║
  ╚══════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    echo -e "  ${CYAN}${BOLD}URLs:${NC}"
    echo -e "    Webhook API:   ${BOLD}https://${API_DOMAIN}/webhook/{tenant_id}${NC}"
    echo -e "    Painel Admin:  ${BOLD}https://${ADMIN_DOMAIN}${NC}"
    echo -e "    Swagger:       ${BOLD}https://${API_DOMAIN}/docs${NC}"
    echo
    echo -e "  ${CYAN}${BOLD}Próximos passos:${NC}"
    echo    "    1. Verifique o DNS se ainda não configurou:"
    echo    "         A  api.${DOMAIN}   →  ${server_ip}"
    echo    "         A  admin.${DOMAIN} →  ${server_ip}"
    echo    "    2. Crie o primeiro tenant:"
    echo    "         POST https://${API_DOMAIN}/admin/tenants"
    echo    "    3. Configure seu gateway WhatsApp com o webhook URL"
    echo    "    4. Acesse o painel admin em https://${ADMIN_DOMAIN}"
    echo
    echo -e "  ${YELLOW}${BOLD}Credenciais salvas em:${NC} ${SCRIPT_DIR}/CREDENCIAIS.txt"
    echo
    echo -e "  ${BOLD}Logs:${NC}       docker compose logs -f api"
    echo -e "  ${BOLD}Status:${NC}     docker compose ps"
    echo -e "  ${BOLD}Atualizar:${NC}  git pull && docker compose up -d --build"
    echo
}

# ── Fluxo principal ───────────────────────────────────────────────────────────
main() {
    banner
    check_root
    cd "${SCRIPT_DIR}"
    : > "$LOG_FILE"  # limpa/cria o log

    install_system_deps
    install_docker
    check_structure       # verifica agents/, llm/, api/, etc. antes de qualquer build
    configure_firewall
    collect_config
    check_dns
    hash_admin_password
    write_env
    write_nginx_http
    issue_ssl
    start_services
    health_check
    save_credentials
    print_summary
}

main "$@"
