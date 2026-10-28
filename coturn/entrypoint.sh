#!/bin/sh
set -eu

if [ -z "${COTURN_AUTH_SECRET:-}" ] || [ -z "${TURN_REALM:-}" ]; then
  echo "coturn: COTURN_AUTH_SECRET and TURN_REALM are required" >&2
  exit 1
fi

MIN_PORT="${TURN_MIN_PORT:-49160}"
MAX_PORT="${TURN_MAX_PORT:-49200}"
CONF="/tmp/turnserver.conf"

cat > "$CONF" <<EOF
listening-port=3478
tls-listening-port=5349
min-port=${MIN_PORT}
max-port=${MAX_PORT}
use-auth-secret
static-auth-secret=${COTURN_AUTH_SECRET}
realm=${TURN_REALM}
fingerprint
no-cli
no-multicast-peers
no-tcp-relay
no-rfc5780
no-stun-backward-compatibility
response-origin-only-with-rfc5780
user-quota=12
total-quota=200
stale-nonce=600
log-file=stdout
simple-log
EOF

if [ -n "${TURN_EXTERNAL_IP:-}" ]; then
  if [ -n "${TURN_PRIVATE_IP:-}" ]; then
    echo "external-ip=${TURN_EXTERNAL_IP}/${TURN_PRIVATE_IP}" >> "$CONF"
    echo "listening-ip=${TURN_PRIVATE_IP}" >> "$CONF"
    echo "allowed-peer-ip=${TURN_PRIVATE_IP}" >> "$CONF"
  else
    echo "external-ip=${TURN_EXTERNAL_IP}" >> "$CONF"
  fi
fi

# Mitigate TURN as SSRF into operator LAN / metadata / special-use ranges
for range in \
  "0.0.0.0-0.255.255.255" \
  "10.0.0.0-10.255.255.255" \
  "100.64.0.0-100.127.255.255" \
  "127.0.0.0-127.255.255.255" \
  "169.254.0.0-169.254.255.255" \
  "172.16.0.0-172.31.255.255" \
  "192.0.0.0-192.0.0.255" \
  "192.0.2.0-192.0.2.255" \
  "192.88.99.0-192.88.99.255" \
  "192.168.0.0-192.168.255.255" \
  "198.18.0.0-198.19.255.255" \
  "198.51.100.0-198.51.100.255" \
  "203.0.113.0-203.0.113.255" \
  "224.0.0.0-239.255.255.255" \
  "240.0.0.0-255.255.255.255" \
  "::1" \
  "::ffff:0:0-::ffff:ffff:ffff" \
  "64:ff9b::-64:ff9b::ffff:ffff" \
  "100::-100::ffff:ffff:ffff:ffff" \
  "2001::-2001:1ff:ffff:ffff:ffff:ffff:ffff" \
  "2002::-2002:ffff:ffff:ffff:ffff:ffff:ffff:ffff" \
  "fc00::-fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff" \
  "fe80::-febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
do
  echo "denied-peer-ip=${range}" >> "$CONF"
done

if [ -f /certs/fullchain.pem ] && [ -f /certs/privkey.pem ]; then
  cat >> "$CONF" <<EOF
cert=/certs/fullchain.pem
pkey=/certs/privkey.pem
no-tlsv1
no-tlsv1_1
EOF
else
  echo "coturn: TLS certs missing in /certs — turns:5349 disabled (no-tls)" >&2
  echo "no-tls" >> "$CONF"
fi

exec turnserver -c "$CONF"
