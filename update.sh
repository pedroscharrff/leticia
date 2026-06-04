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

# ── 1.5 Provisionamento incremental: MinIO + subdomínio storage ───────────────
# Idempotente: detecta o que falta e adiciona. Seguro de rodar várias vezes.
step "1.5/5  Provisionando MinIO + subdomínio storage (idempotente)"

ENV_FILE="${SCRIPT_DIR}/.env"
[[ -f "$ENV_FILE" ]] || error ".env não encontrado em ${ENV_FILE}"

# Descobre domínio base a partir de VITE_API_URL=https://api.<DOMAIN>
API_URL=$(grep -E "^VITE_API_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
API_DOMAIN=${API_URL#https://}
API_DOMAIN=${API_DOMAIN#http://}
BASE_DOMAIN=${API_DOMAIN#api.}
ADMIN_DOMAIN="admin.${BASE_DOMAIN}"
STORAGE_DOMAIN="storage.${BASE_DOMAIN}"

if [[ -z "$BASE_DOMAIN" || "$BASE_DOMAIN" == "$API_DOMAIN" ]]; then
    error "Não consegui detectar o domínio base a partir do .env (VITE_API_URL=${API_URL}). Abortando para não corromper a config."
fi
info "Domínios detectados: api=${API_DOMAIN}  admin=${ADMIN_DOMAIN}  storage=${STORAGE_DOMAIN}"

# ── 1.5.1 Garante vars do MinIO no .env (sem tocar nas existentes) ────────────
if ! grep -q "^MINIO_SECRET_KEY=" "$ENV_FILE"; then
    info "Adicionando vars do MinIO ao .env (segredo gerado automaticamente)..."
    MINIO_SECRET=$(openssl rand -hex 24)
    {
        echo ""
        echo "# ─── MinIO (object storage — adicionado por update.sh) ─────────────────"
        echo "MINIO_ENDPOINT='minio:9000'"
        echo "MINIO_ACCESS_KEY='farmacia'"
        echo "MINIO_SECRET_KEY='${MINIO_SECRET}'"
        echo "MINIO_BUCKET='offers-media'"
        echo "MINIO_SECURE='false'"
        echo "MINIO_PUBLIC_URL='https://${STORAGE_DOMAIN}'"
    } >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    success "Vars do MinIO adicionadas ao .env"
else
    info ".env já tem MINIO_SECRET_KEY — preservando o segredo existente."
fi

# ── 1.5.2 Verifica DNS do storage ─────────────────────────────────────────────
STORAGE_IP=$(getent hosts "${STORAGE_DOMAIN}" | awk '{print $1}' | head -1 || true)
SERVER_IP=$(curl -s --max-time 5 ifconfig.me || echo "")
if [[ -z "$STORAGE_IP" ]]; then
    warn "DNS de ${STORAGE_DOMAIN} ainda não propagado."
    warn "Configure no registrador:  A  ${STORAGE_DOMAIN}  →  ${SERVER_IP:-IP_DO_SERVIDOR}"
    read -rp "  Continuar mesmo assim (cert + nginx só funcionarão após DNS)? [s/N]: " cont
    [[ "${cont,,}" == "s" ]] || error "Configure o DNS e rode update.sh de novo."
elif [[ -n "$SERVER_IP" && "$STORAGE_IP" != "$SERVER_IP" ]]; then
    warn "DNS de ${STORAGE_DOMAIN} aponta para ${STORAGE_IP}, mas este servidor é ${SERVER_IP}."
    warn "Provavelmente é Cloudflare Proxy — se for, OK. Senão, corrija o A record."
else
    success "DNS de ${STORAGE_DOMAIN} aponta corretamente."
fi

# ── 1.5.3 Sobe o MinIO antes de tudo (precisa estar pronto p/ a API usar) ─────
if ! docker compose ps --format "{{.Name}}\t{{.State}}" 2>/dev/null | grep -q "minio.*running"; then
    info "Iniciando container do MinIO..."
    docker compose up -d --no-deps minio
    # Aguarda ready
    MINIO_TRIES=0
    while [[ $MINIO_TRIES -lt 20 ]]; do
        if docker compose exec -T minio mc ready local &>/dev/null \
            || docker compose exec -T minio curl -sf http://localhost:9000/minio/health/ready &>/dev/null; then
            break
        fi
        sleep 2
        ((MINIO_TRIES++))
    done
    success "MinIO iniciado"
else
    info "MinIO já está rodando — pulando."
fi

# ── 1.5.4 Expande certificado SSL para cobrir storage.${DOMAIN} ───────────────
CERT_PATH="/etc/letsencrypt/live/${API_DOMAIN}/fullchain.pem"
CERT_COVERS_STORAGE=false
if docker compose run --rm --entrypoint sh certbot -c \
    "openssl x509 -in ${CERT_PATH} -noout -text 2>/dev/null | grep -q ${STORAGE_DOMAIN}"; then
    CERT_COVERS_STORAGE=true
fi

if [[ "$CERT_COVERS_STORAGE" == "true" ]]; then
    success "Certificado SSL já cobre ${STORAGE_DOMAIN} — sem reemissão."
else
    info "Expandindo certificado SSL para incluir ${STORAGE_DOMAIN}..."
    # Precisa que o nginx HTTP (porta 80) responda ao ACME — config atual já cobre.
    # Mas o redirect HTTP→HTTPS pode interceptar. Solução: o bloco /.well-known/
    # já está fora do redirect 301 na config gerada pelo deploy.sh.
    if ! timeout 180 docker compose run --rm --entrypoint certbot certbot certonly \
        --webroot --webroot-path=/var/www/certbot \
        --expand --non-interactive --agree-tos \
        --cert-name "${API_DOMAIN}" \
        -d "${API_DOMAIN}" -d "${ADMIN_DOMAIN}" -d "${STORAGE_DOMAIN}"; then
        error "Falha ao expandir certificado. Verifique DNS + porta 80 aberta."
    fi
    success "Certificado expandido"
fi

# ── 1.5.5 Garante que o nginx config tem o server block do storage ────────────
NGINX_CONF="${SCRIPT_DIR}/nginx/default.conf"
if ! grep -q "${STORAGE_DOMAIN}" "$NGINX_CONF" 2>/dev/null; then
    info "Atualizando nginx/default.conf com o server block do storage..."

    # 1) Adiciona STORAGE_DOMAIN aos server_name das duas linhas que listam api+admin
    sed -i "s/server_name ${API_DOMAIN} ${ADMIN_DOMAIN};/server_name ${API_DOMAIN} ${ADMIN_DOMAIN} ${STORAGE_DOMAIN};/g" "$NGINX_CONF"

    # 2) Anexa o server block do storage no final
    cat >> "$NGINX_CONF" << EOF

# ── Storage (MinIO) — https://${STORAGE_DOMAIN} ──────────────────────────────
# Adicionado por update.sh — serve mídias das ofertas via URL pública.
server {
    listen 443 ssl http2;
    server_name ${STORAGE_DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${API_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${API_DOMAIN}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options    nosniff                                         always;

    client_max_body_size 20M;
    resolver 127.0.0.11 valid=10s ipv6=off;

    location / {
        set \$upstream_minio minio:9000;
        proxy_pass            http://\$upstream_minio;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        proxy_buffering       off;
        proxy_request_buffering off;
    }
}
EOF
    success "nginx/default.conf atualizado"

    # Valida e recarrega
    if docker compose exec -T nginx nginx -t &>/dev/null; then
        docker compose exec -T nginx nginx -s reload
        success "nginx recarregado com o novo bloco do storage"
    else
        warn "nginx -t falhou. Restaurando config anterior NÃO é automático — verifique:"
        warn "  docker compose exec nginx nginx -t"
    fi
else
    info "nginx config já contém ${STORAGE_DOMAIN} — sem alteração."
fi

# ── 1.6 Provisionamento incremental: Prometheus + subdomínio metrics ─────────
# Idempotente: detecta o que falta (cert, htpasswd, nginx block, .env) e
# adiciona. Seguro de rodar várias vezes.
step "1.6/5  Provisionando Prometheus + subdomínio metrics (idempotente)"

METRICS_DOMAIN="metrics.${BASE_DOMAIN}"
HTPASSWD_FILE="${SCRIPT_DIR}/nginx/htpasswd"
METRICS_CONF="${SCRIPT_DIR}/nginx/metrics.conf"

# ── 1.6.1 Verifica DNS de metrics.${DOMAIN} ──────────────────────────────────
METRICS_IP=$(getent hosts "${METRICS_DOMAIN}" | awk '{print $1}' | head -1 || true)
if [[ -z "$METRICS_IP" ]]; then
    warn "DNS de ${METRICS_DOMAIN} ainda não propagado."
    warn "Configure no registrador:  A  ${METRICS_DOMAIN}  →  ${SERVER_IP:-IP_DO_SERVIDOR}"
    read -rp "  Continuar mesmo assim (cert + nginx só funcionarão após DNS)? [s/N]: " cont
    [[ "${cont,,}" == "s" ]] || error "Configure o DNS e rode update.sh de novo."
elif [[ -n "$SERVER_IP" && "$METRICS_IP" != "$SERVER_IP" ]]; then
    warn "DNS de ${METRICS_DOMAIN} aponta para ${METRICS_IP}, mas este servidor é ${SERVER_IP}."
    warn "Provavelmente é Cloudflare Proxy — se for, OK. Senão, corrija o A record."
else
    success "DNS de ${METRICS_DOMAIN} aponta corretamente."
fi

# ── 1.6.2 Gera basic auth (htpasswd) se ainda não existir ────────────────────
# Detecta arquivo "vazio" (só comentários) também — o repo vem com placeholder.
HAS_REAL_HTPASSWD=false
if [[ -s "$HTPASSWD_FILE" ]] && grep -vE "^\s*(#|$)" "$HTPASSWD_FILE" | grep -q ":"; then
    HAS_REAL_HTPASSWD=true
fi

if [[ "$HAS_REAL_HTPASSWD" == "false" ]]; then
    info "Gerando basic auth para o Prometheus..."
    METRICS_USER="${METRICS_USER:-admin}"
    METRICS_PASSWORD="${METRICS_PASSWORD:-$(openssl rand -hex 16)}"

    # Bcrypt via container alpine (sem depender do htpasswd local)
    docker run --rm httpd:2.4-alpine htpasswd -nbB "${METRICS_USER}" "${METRICS_PASSWORD}" \
        2>/dev/null > "${HTPASSWD_FILE}" \
        || error "Falha ao gerar htpasswd. Verifique se o Docker está rodando."
    chmod 644 "${HTPASSWD_FILE}"

    # Persiste no .env pra update.sh seguinte não rotacionar
    if ! grep -q "^METRICS_PASSWORD=" "$ENV_FILE"; then
        {
            echo ""
            echo "# ─── Prometheus basic auth (gerado por update.sh) ───"
            echo "METRICS_USER='${METRICS_USER}'"
            echo "METRICS_PASSWORD='${METRICS_PASSWORD}'"
            echo "METRICS_DOMAIN='${METRICS_DOMAIN}'"
        } >> "$ENV_FILE"
        chmod 600 "$ENV_FILE"
    fi
    success "htpasswd gerado — user=${METRICS_USER} (senha em .env como METRICS_PASSWORD)"
else
    info "htpasswd já existe — preservando credenciais."
fi

# ── 1.6.2.5 Pré-cria metrics.conf SÓ COM :80 ACME ────────────────────────────
# Garante que nginx tenha server_name pra metrics.${DOMAIN} ANTES do certbot
# expandir (mesmo padrão que grafana.conf abaixo).
if [[ ! -f "$METRICS_CONF" ]] || ! grep -q "${METRICS_DOMAIN}" "$METRICS_CONF" 2>/dev/null; then
    info "Escrevendo metrics.conf temporário (:80 ACME only) pra cert expansion..."
    cat > "$METRICS_CONF" << EOF
# ─── TEMPORÁRIO (update.sh) — sobrescrito após certbot expandir ─────────────
server {
    listen 80;
    server_name ${METRICS_DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; try_files \$uri =404; }
    location / { return 200 "Awaiting SSL...\n"; }
}
EOF
    docker compose exec -T nginx nginx -s reload 2>/dev/null \
        || docker compose restart nginx
    sleep 2
fi

# ── 1.6.3 Expande certificado SSL para cobrir metrics.${DOMAIN} ──────────────
CERT_COVERS_METRICS=false
if docker compose run --rm --entrypoint sh certbot -c \
    "openssl x509 -in ${CERT_PATH} -noout -text 2>/dev/null | grep -q ${METRICS_DOMAIN}"; then
    CERT_COVERS_METRICS=true
fi

if [[ "$CERT_COVERS_METRICS" == "true" ]]; then
    success "Certificado SSL já cobre ${METRICS_DOMAIN} — sem reemissão."
else
    info "Expandindo certificado SSL para incluir ${METRICS_DOMAIN}..."
    if ! timeout 180 docker compose run --rm --entrypoint certbot certbot certonly \
        --webroot --webroot-path=/var/www/certbot \
        --expand --non-interactive --agree-tos \
        --cert-name "${API_DOMAIN}" \
        -d "${API_DOMAIN}" -d "${ADMIN_DOMAIN}" -d "${STORAGE_DOMAIN}" -d "${METRICS_DOMAIN}"; then
        warn "Falha ao expandir cert p/ ${METRICS_DOMAIN}. Pode tentar manualmente depois."
        warn "  Verifique DNS + porta 80 aberta."
    else
        success "Certificado expandido"
    fi
fi

# ── 1.6.4 Garante metrics.conf (vhost nginx do Prometheus) ───────────────────
# Não bate com default.conf porque está em arquivo separado. nginx carrega
# tudo em /etc/nginx/conf.d/*.conf automaticamente.
if [[ ! -f "$METRICS_CONF" ]] || grep -q "^# ─── TEMPORÁRIO" "$METRICS_CONF" 2>/dev/null \
   || ! grep -q "listen 443" "$METRICS_CONF" 2>/dev/null; then
    info "Escrevendo ${METRICS_CONF} (vhost completo HTTPS)..."
    cat > "$METRICS_CONF" << EOF
# ─── Gerado por update.sh ─────────────────────────────────────────────────────
# https://${METRICS_DOMAIN} → Prometheus (basic auth via /etc/nginx/htpasswd)

server {
    listen 443 ssl http2;
    server_name ${METRICS_DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${API_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${API_DOMAIN}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options           DENY                                            always;
    add_header X-Content-Type-Options    nosniff                                         always;
    add_header Referrer-Policy           "no-referrer"                                   always;

    auth_basic           "Metrics — restricted";
    auth_basic_user_file /etc/nginx/htpasswd;

    resolver 127.0.0.11 valid=10s ipv6=off;

    location / {
        set \$upstream_prom prometheus:9090;
        proxy_pass            http://\$upstream_prom;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
        proxy_read_timeout    60s;
    }
}

server {
    listen 80;
    server_name ${METRICS_DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; try_files \$uri =404; }
    location / { return 301 https://\$host\$request_uri; }
}
EOF
    success "metrics.conf escrito"
else
    info "metrics.conf já existe — preservando."
fi

# ── 1.6.5 Sobe Prometheus + Grafana ──────────────────────────────────────────
# `up -d` é idempotente: se já estiver rodando com a config atual, no-op; se
# o prometheus.yml ou docker-compose.yml mudou, recria.
info "Subindo Prometheus + Grafana..."
docker compose up -d --no-deps prometheus grafana

# Sanity check do prometheus.yml antes do reload do nginx
if ! docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml &>/dev/null; then
    warn "prometheus.yml inválido — verifique: docker compose logs prometheus"
fi

# ── 1.6.6 Recarrega nginx pra carregar metrics.conf + htpasswd ──────────────
if docker compose exec -T nginx nginx -t &>/dev/null; then
    docker compose exec -T nginx nginx -s reload
    success "nginx recarregado (vhost ${METRICS_DOMAIN} ativo)"
else
    warn "nginx -t falhou após adicionar metrics.conf — config NÃO recarregada."
    warn "  Verifique: docker compose exec nginx nginx -t"
fi

# ── 1.7 Provisionamento incremental: Grafana + subdomínio grafana ────────────
# Idempotente. Grafana TEM auth próprio — sem basic auth no nginx.
step "1.7/5  Provisionando Grafana + subdomínio grafana (idempotente)"

GRAFANA_DOMAIN="grafana.${BASE_DOMAIN}"
GRAFANA_CONF="${SCRIPT_DIR}/nginx/grafana.conf"

# ── 1.7.1 DNS check ──────────────────────────────────────────────────────────
GRAFANA_IP=$(getent hosts "${GRAFANA_DOMAIN}" | awk '{print $1}' | head -1 || true)
if [[ -z "$GRAFANA_IP" ]]; then
    warn "DNS de ${GRAFANA_DOMAIN} ainda não propagado."
    warn "Configure no registrador:  A  ${GRAFANA_DOMAIN}  →  ${SERVER_IP:-IP_DO_SERVIDOR}"
    read -rp "  Continuar mesmo assim (cert + nginx só funcionarão após DNS)? [s/N]: " cont
    [[ "${cont,,}" == "s" ]] || error "Configure o DNS e rode update.sh de novo."
elif [[ -n "$SERVER_IP" && "$GRAFANA_IP" != "$SERVER_IP" ]]; then
    warn "DNS de ${GRAFANA_DOMAIN} aponta para ${GRAFANA_IP}, mas este servidor é ${SERVER_IP}."
    warn "Provavelmente é Cloudflare Proxy — se for, OK."
else
    success "DNS de ${GRAFANA_DOMAIN} aponta corretamente."
fi

# ── 1.7.2 Garante GRAFANA_PASSWORD + GRAFANA_ROOT_URL no .env ─────────────────
# Se já tem GRAFANA_PASSWORD, preserva. Se tem METRICS_PASSWORD (do passo 1.6),
# reusa (o usuário pediu mesma senha). Senão, gera nova.
if ! grep -q "^GRAFANA_PASSWORD=" "$ENV_FILE"; then
    if grep -q "^METRICS_PASSWORD=" "$ENV_FILE"; then
        GRAFANA_PWD=$(grep "^METRICS_PASSWORD=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d "'\"")
        info "Reusando METRICS_PASSWORD do .env como senha do Grafana."
    else
        GRAFANA_PWD=$(openssl rand -hex 16)
        info "Gerada senha aleatória do Grafana."
    fi
    {
        echo ""
        echo "# ─── Grafana (adicionado por update.sh) ───"
        echo "GRAFANA_PASSWORD='${GRAFANA_PWD}'"
        echo "GRAFANA_ROOT_URL='https://${GRAFANA_DOMAIN}'"
    } >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    success "GRAFANA_PASSWORD/ROOT_URL adicionados ao .env"
else
    # Garante GRAFANA_ROOT_URL mesmo se GRAFANA_PASSWORD já existia
    if ! grep -q "^GRAFANA_ROOT_URL=" "$ENV_FILE"; then
        echo "GRAFANA_ROOT_URL='https://${GRAFANA_DOMAIN}'" >> "$ENV_FILE"
        chmod 600 "$ENV_FILE"
    fi
    info "GRAFANA_PASSWORD já existe — preservando."
fi

# ── 1.7.2.5 Pré-cria grafana.conf SÓ COM :80 ACME ────────────────────────────
# Sem isso, no momento do `certbot --expand` o nginx não tem server_name pra
# grafana.${DOMAIN} → Let's Encrypt chega no http://grafana.../.well-known/...,
# nginx faz 301 pra https → 443 sem cert → handshake fail → challenge falha.
# Esta config é temporária e sobrescrita em 1.7.4 com o vhost completo.
if [[ ! -f "$GRAFANA_CONF" ]] || grep -q "^# PLACEHOLDER" "$GRAFANA_CONF" 2>/dev/null \
   || ! grep -q "${GRAFANA_DOMAIN}" "$GRAFANA_CONF" 2>/dev/null; then
    info "Escrevendo grafana.conf temporário (:80 ACME only) pra cert expansion..."
    cat > "$GRAFANA_CONF" << EOF
# ─── TEMPORÁRIO (update.sh) — sobrescrito após certbot expandir ─────────────
server {
    listen 80;
    server_name ${GRAFANA_DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; try_files \$uri =404; }
    location / { return 200 "Awaiting SSL...\n"; }
}
EOF
    docker compose exec -T nginx nginx -s reload 2>/dev/null \
        || docker compose restart nginx
    sleep 2
fi

# ── 1.7.3 Expande cert SSL pra cobrir grafana.${DOMAIN} ──────────────────────
CERT_COVERS_GRAFANA=false
if docker compose run --rm --entrypoint sh certbot -c \
    "openssl x509 -in ${CERT_PATH} -noout -text 2>/dev/null | grep -q ${GRAFANA_DOMAIN}"; then
    CERT_COVERS_GRAFANA=true
fi

if [[ "$CERT_COVERS_GRAFANA" == "true" ]]; then
    success "Certificado SSL já cobre ${GRAFANA_DOMAIN}."
else
    info "Expandindo cert SSL pra incluir ${GRAFANA_DOMAIN}..."
    # Inclui TODOS os subdomínios que já estão no cert atual + grafana.
    # Certbot exige que o --expand traga a lista COMPLETA de SANs novos.
    EXPAND_DOMAINS=("-d" "${API_DOMAIN}" "-d" "${ADMIN_DOMAIN}" "-d" "${STORAGE_DOMAIN}" "-d" "${GRAFANA_DOMAIN}")
    if docker compose run --rm --entrypoint sh certbot -c \
        "openssl x509 -in ${CERT_PATH} -noout -text 2>/dev/null | grep -q metrics.${BASE_DOMAIN}"; then
        EXPAND_DOMAINS+=("-d" "metrics.${BASE_DOMAIN}")
    fi
    if ! timeout 180 docker compose run --rm --entrypoint certbot certbot certonly \
        --webroot --webroot-path=/var/www/certbot \
        --expand --non-interactive --agree-tos \
        --cert-name "${API_DOMAIN}" \
        "${EXPAND_DOMAINS[@]}"; then
        warn "Falha ao expandir cert p/ ${GRAFANA_DOMAIN}. Tente manualmente depois."
    else
        success "Certificado expandido"
    fi
fi

# ── 1.7.4 Escreve grafana.conf (vhost nginx) ─────────────────────────────────
# Sem basic auth — Grafana tem auth próprio. WebSocket upgrade pra live updates.
if [[ ! -f "$GRAFANA_CONF" ]] || grep -q "^# PLACEHOLDER\|^# ─── TEMPORÁRIO" "$GRAFANA_CONF" 2>/dev/null \
   || ! grep -q "listen 443" "$GRAFANA_CONF" 2>/dev/null; then
    info "Escrevendo ${GRAFANA_CONF} (vhost completo HTTPS)..."
    cat > "$GRAFANA_CONF" << EOF
# ─── Gerado por update.sh ─────────────────────────────────────────────────────
# https://${GRAFANA_DOMAIN} → Grafana (auth do Grafana, não basic auth nginx)

server {
    listen 443 ssl http2;
    server_name ${GRAFANA_DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${API_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${API_DOMAIN}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options    nosniff                                         always;

    # Grafana faz upload de dashboards JSON grandes — 32M de folga
    client_max_body_size 32M;

    resolver 127.0.0.11 valid=10s ipv6=off;

    # Live updates (WebSocket) — caminho /api/live/ws precisa de upgrade
    location /api/live/ {
        set \$upstream_grafana grafana:3000;
        proxy_pass            http://\$upstream_grafana;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      Upgrade           \$http_upgrade;
        proxy_set_header      Connection        "upgrade";
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
    }

    location / {
        set \$upstream_grafana grafana:3000;
        proxy_pass            http://\$upstream_grafana;
        proxy_http_version    1.1;
        proxy_set_header      Host              \$host;
        proxy_set_header      X-Real-IP         \$remote_addr;
        proxy_set_header      X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto \$scheme;
        proxy_read_timeout    60s;
    }
}

server {
    listen 80;
    server_name ${GRAFANA_DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; try_files \$uri =404; }
    location / { return 301 https://\$host\$request_uri; }
}
EOF
    success "grafana.conf escrito"
else
    info "grafana.conf já existe — preservando."
fi

# ── 1.7.5 Recria Grafana pra pegar GF_SERVER_ROOT_URL atualizado ────────────
# `up -d --force-recreate` é necessário porque env vars só são lidas no boot;
# se GRAFANA_ROOT_URL acabou de mudar, sem recreate o Grafana ainda serve
# com a URL antiga (afeta links de share, OAuth redirect, e-mails).
info "Recriando Grafana com nova GF_SERVER_ROOT_URL=https://${GRAFANA_DOMAIN}..."
docker compose up -d --no-deps --force-recreate grafana

# ── 1.7.6 Reload nginx ──────────────────────────────────────────────────────
if docker compose exec -T nginx nginx -t &>/dev/null; then
    docker compose exec -T nginx nginx -s reload
    success "nginx recarregado (vhost ${GRAFANA_DOMAIN} ativo)"
else
    warn "nginx -t falhou — config NÃO recarregada. Veja: docker compose exec nginx nginx -t"
fi

# ── 2. Build apenas das imagens da aplicação ──────────────────────────────────
step "2/5  Fazendo build das imagens"

info "Rebuilding: api, worker, beat, admin (infra não muda)"
# beat compartilha imagem com o worker (mesmo Dockerfile), mas precisa entrar
# explicitamente no build pra garantir que o cache fica coerente entre os dois
# (Compose builda por service). E precisa REINICIAR sempre que o
# `beat_schedule` em workers/celery_app.py mudar — o scheduler só lê na boot.
docker compose build --pull api worker beat admin

success "Build concluído"

# ── 3. Subir containers + rodar migrations pendentes ──────────────────────────
step "3/5  Aplicando atualização"

# Garante que PgBouncer está rodando ANTES de api/worker — eles têm
# depends_on=pgbouncer e o DATABASE_URL aponta pra `pgbouncer:5432`. Sem o
# bouncer no ar, o api/worker derruba com "Temporary failure in name resolution".
# Idempotente: se já está rodando, docker compose não faz nada.
info "Garantindo PgBouncer..."
docker compose up -d --no-deps pgbouncer

PGB_TRIES=0
while [[ $PGB_TRIES -lt 15 ]]; do
    if docker compose ps --format "{{.Name}}\t{{.Status}}" 2>/dev/null \
        | grep -E "pgbouncer.*(healthy|Up)" >/dev/null; then
        break
    fi
    sleep 2
    ((PGB_TRIES++))
done
success "PgBouncer pronto"

info "Subindo novos containers..."
docker compose up -d --no-deps api worker beat admin

info "Recarregando nginx (atualiza IP dos novos containers)..."
docker compose exec -T nginx nginx -s reload 2>/dev/null \
    || docker compose restart nginx

info "Removendo imagens antigas não utilizadas..."
docker image prune -f &>/dev/null || true

success "Containers atualizados"

# ── 4. Rodar migrations pendentes ─────────────────────────────────────────────
step "4/5  Aplicando migrations do banco"

info "Esperando Postgres ficar pronto..."
PG_TRIES=0
while [[ $PG_TRIES -lt 15 ]]; do
    if docker compose exec -T postgres pg_isready -U farmacia -d saas_farmacia &>/dev/null; then
        break
    fi
    sleep 2
    ((PG_TRIES++))
done

info "Executando scripts/run_migrations.py (idempotente — só aplica pendentes)..."
if docker compose exec -T api python /app/scripts/run_migrations.py; then
    success "Migrations aplicadas"
else
    warn "Falha ao rodar migrations. Verifique manualmente:"
    warn "  docker compose exec api python /app/scripts/run_migrations.py"
fi

# ── 5. Health check ───────────────────────────────────────────────────────────
step "5/5  Verificando saúde"

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
echo -e "  Beat (scheduler):    ${CYAN}docker compose logs -f beat${NC}"
echo -e "  Métricas:            ${CYAN}https://${METRICS_DOMAIN}${NC}  (basic auth: cat .env | grep METRICS_)"
echo -e "  Grafana:             ${CYAN}https://${GRAFANA_DOMAIN}${NC}  (admin / cat .env | grep GRAFANA_PASSWORD)"
echo
