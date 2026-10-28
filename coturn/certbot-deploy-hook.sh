#!/bin/sh
# Installed as /etc/letsencrypt/renewal-hooks/deploy/rumble-coturn.sh
# Placeholders __INSTALL_DIR__ and __DOMAIN__ are replaced by the installer.

DOMAIN="__DOMAIN__"
INSTALL_DIR="__INSTALL_DIR__"
CERTS_DIR="${INSTALL_DIR}/coturn/certs"

case " ${RENEWED_DOMAINS:-} " in
  *" ${DOMAIN} "*) ;;
  *) exit 0 ;;
esac

LE_LIVE="/etc/letsencrypt/live/${DOMAIN}"
if [ ! -f "${LE_LIVE}/fullchain.pem" ] || [ ! -f "${LE_LIVE}/privkey.pem" ]; then
  echo "rumble-coturn hook: cert files missing under ${LE_LIVE}" >&2
  exit 1
fi

mkdir -p "${CERTS_DIR}"
cp "${LE_LIVE}/fullchain.pem" "${CERTS_DIR}/fullchain.pem"
cp "${LE_LIVE}/privkey.pem" "${CERTS_DIR}/privkey.pem"
chown 65534:65534 "${CERTS_DIR}/fullchain.pem" "${CERTS_DIR}/privkey.pem" 2>/dev/null || true
chmod 644 "${CERTS_DIR}/fullchain.pem"
chmod 600 "${CERTS_DIR}/privkey.pem"

docker restart rumbleserver_coturn 2>/dev/null || true
