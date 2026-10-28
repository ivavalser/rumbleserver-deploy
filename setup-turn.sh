#!/bin/bash
# Idempotent coturn bootstrap: .env TURN_*, UFW, LE cert copy, certbot deploy hook.
# Invoked from ./prod.sh (when TURN_AUTO_SETUP=true or COTURN_AUTH_SECRET set) or installer TURN step.
set -euo pipefail

QUIET=0
FROM_INSTALLER=0
AUTO_PROD=0
for arg in "$@"; do
  case "$arg" in
    --quiet) QUIET=1 ;;
    --from-installer) FROM_INSTALLER=1 ;;
    --auto) AUTO_PROD=1 ;;
  esac
done

log() {
  if [ "$QUIET" -eq 0 ]; then
    echo "$@"
  fi
}

warn() {
  echo "$@" >&2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/coturn/entrypoint.sh" ] && [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
  INSTALL_DIR="$SCRIPT_DIR"
  COTURN_DIR="$SCRIPT_DIR/coturn"
elif [ -f "$SCRIPT_DIR/coturn/entrypoint.sh" ] && [ -f "$SCRIPT_DIR/docker-compose.prod.yml" ]; then
  INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  COTURN_DIR="$SCRIPT_DIR/coturn"
else
  warn "setup-turn: cannot find coturn/ (run from operator bundle or deploy/)"
  exit 1
fi

ENV_FILE="$INSTALL_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  warn "setup-turn: missing $ENV_FILE"
  exit 1
fi

env_get() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true
}

env_upsert() {
  local key="$1"
  local val="$2"
  local tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$val" '
      BEGIN { eq = "=" }
      $0 ~ "^" k eq { print k eq v; next }
      { print }
    ' "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
}

domain_from_env() {
  local realm allowed part
  realm="$(env_get TURN_REALM)"
  if [ -n "$realm" ]; then
    echo "$realm"
    return
  fi
  allowed="$(env_get ALLOWED_HOSTS)"
  IFS=',' read -ra parts <<< "$allowed"
  for part in "${parts[@]}"; do
    part="${part// /}"
    if [ -n "$part" ] && [ "$part" != "localhost" ] && [ "$part" != "127.0.0.1" ]; then
      echo "$part"
      return
    fi
  done
  echo ""
}

public_ip() {
  curl -fsSL --max-time 10 https://api.ipify.org 2>/dev/null || true
}

private_ip() {
  ip -4 route get 1.1.1.1 2>/dev/null | awk '/src/{print $7; exit}' || true
}

coturn_urls() {
  local d="$1"
  printf 'stun:%s:3478,turn:%s:3478?transport=udp,turn:%s:3478?transport=tcp,turns:%s:5349?transport=tcp' "$d" "$d" "$d" "$d"
}

AUTO="$(env_get TURN_AUTO_SETUP)"
SECRET="$(env_get COTURN_AUTH_SECRET)"

if [ "$AUTO" = "false" ]; then
  log "setup-turn: skipped (TURN_AUTO_SETUP=false)"
  exit 0
fi

DOMAIN="$(domain_from_env)"
LE_CERT="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"

RUN=0
if [ "$FROM_INSTALLER" -eq 1 ]; then
  RUN=1
elif [ -n "$SECRET" ]; then
  RUN=1
elif [ "$AUTO" = "true" ] || [ "$AUTO" = "1" ]; then
  RUN=1
elif [ "$AUTO_PROD" -eq 1 ] && [ -n "$DOMAIN" ] && [ -f "$LE_CERT" ]; then
  RUN=1
  log "setup-turn: auto (HTTPS + domain ${DOMAIN})"
fi

if [ "$RUN" -eq 0 ]; then
  exit 0
fi

if [ -z "$DOMAIN" ]; then
  warn "setup-turn: could not determine domain (ALLOWED_HOSTS / TURN_REALM)"
  exit 1
fi

if [ "$FROM_INSTALLER" -eq 1 ] && [ ! -f "$LE_CERT" ]; then
  warn "setup-turn: Let's Encrypt cert not found at $LE_CERT (complete HTTPS first)"
  exit 1
fi

PUB="$(env_get TURN_EXTERNAL_IP)"
if [ -z "$PUB" ]; then
  PUB="$(public_ip)"
fi
if [ -z "$PUB" ]; then
  warn "setup-turn: could not detect public IP — set TURN_EXTERNAL_IP in .env"
  exit 1
fi

PRIV="$(private_ip)"
TURN_PRIVATE=""
if [ -n "$PRIV" ] && [ "$PRIV" != "$PUB" ]; then
  TURN_PRIVATE="$PRIV"
fi

if [ -z "$SECRET" ]; then
  SECRET="$(openssl rand -hex 32)"
fi

MIN_PORT="$(env_get TURN_MIN_PORT)"
MAX_PORT="$(env_get TURN_MAX_PORT)"
TTL="$(env_get COTURN_CREDENTIAL_TTL_SECONDS)"
MIN_PORT="${MIN_PORT:-49160}"
MAX_PORT="${MAX_PORT:-49200}"
TTL="${TTL:-300}"

log "setup-turn: updating .env for domain $DOMAIN"
env_upsert FEATURE_TURN_CREDENTIALS true
env_upsert COTURN_AUTH_SECRET "$SECRET"
env_upsert COTURN_URLS "$(coturn_urls "$DOMAIN")"
env_upsert COTURN_CREDENTIAL_TTL_SECONDS "$TTL"
env_upsert TURN_REALM "$DOMAIN"
env_upsert TURN_EXTERNAL_IP "$PUB"
env_upsert TURN_MIN_PORT "$MIN_PORT"
env_upsert TURN_MAX_PORT "$MAX_PORT"
if [ -n "$TURN_PRIVATE" ]; then
  env_upsert TURN_PRIVATE_IP "$TURN_PRIVATE"
elif grep -qE '^TURN_PRIVATE_IP=' "$ENV_FILE"; then
  env_upsert TURN_PRIVATE_IP ""
fi
if [ "$AUTO" = "true" ] || [ "$AUTO" = "1" ] || [ "$FROM_INSTALLER" -eq 1 ] || [ "$AUTO_PROD" -eq 1 ]; then
  env_upsert TURN_AUTO_SETUP true
fi

if command -v ufw >/dev/null 2>&1; then
  log "setup-turn: opening UFW ports for STUN/TURN"
  ufw allow 3478/udp >/dev/null 2>&1 || true
  ufw allow 3478/tcp >/dev/null 2>&1 || true
  ufw allow 5349/tcp >/dev/null 2>&1 || true
  ufw allow "${MIN_PORT}:${MAX_PORT}/udp" >/dev/null 2>&1 || true
fi

HOOK_TEMPLATE="$COTURN_DIR/certbot-deploy-hook.sh"
if [ -f "$LE_CERT" ] && [ -f "$HOOK_TEMPLATE" ]; then
  log "setup-turn: installing certbot deploy hook and copying TLS certs"
  HOOK_PATH="/etc/letsencrypt/renewal-hooks/deploy/rumble-coturn.sh"
  mkdir -p "$(dirname "$HOOK_PATH")"
  sed -e "s|__DOMAIN__|${DOMAIN}|g" -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    "$HOOK_TEMPLATE" > "$HOOK_PATH"
  chmod 755 "$HOOK_PATH"
  RENEWED_DOMAINS="$DOMAIN" /bin/sh "$HOOK_PATH" || true
else
  warn "setup-turn: LE cert or hook template missing — coturn will run without turns:5349 until certs exist"
fi

log "setup-turn: done (run ./prod.sh to pull coturn and restart services)"
log "setup-turn: open 3478/udp+tcp, 5349/tcp, ${MIN_PORT}:${MAX_PORT}/udp in cloud security group if needed"
