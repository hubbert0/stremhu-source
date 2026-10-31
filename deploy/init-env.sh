#!/usr/bin/env sh
# Létrehozza a .env fájlt az .env.example alapján, és kitölti a titkokat.
# Meglévő .env-et nem ír felül.
set -eu

cd "$(dirname "$0")"

if [ -f .env ]; then
  echo "A .env már létezik, nem írom felül."
  exit 0
fi

rand_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

rand_pass() {
  rand_hex | cut -c1-20
}

cp .env.example .env

sed -i "s|^STREMHU_SESSION_SECRET=.*|STREMHU_SESSION_SECRET=$(rand_hex)|" .env
sed -i "s|^AIOSTREAMS_SECRET_KEY=.*|AIOSTREAMS_SECRET_KEY=$(rand_hex)|" .env

AIO_PASS="$(rand_pass)"
sed -i "s|^AIOSTREAMS_AUTH=.*|AIOSTREAMS_AUTH=admin:${AIO_PASS}|" .env

sed -i "s|^LELIBRARY_CONFIG_SECRET=.*|LELIBRARY_CONFIG_SECRET=$(rand_hex)|" .env
sed -i "s|^LELIBRARY_SELFHOST_SECRET=.*|LELIBRARY_SELFHOST_SECRET=$(rand_hex)|" .env
sed -i "s|^LELIBRARY_DB_PASSWORD=.*|LELIBRARY_DB_PASSWORD=$(rand_pass)|" .env
sed -i "s|^NTFY_TOPIC=.*|NTFY_TOPIC=stremhu-$(rand_pass)|" .env

PULT_PASS="$(rand_pass)"
sed -i "s|^PULT_PASSWORD=.*|PULT_PASSWORD=${PULT_PASS}|" .env

chmod 600 .env

echo "Kész: .env létrehozva."
echo "AIOStreams bejelentkezés:  admin / ${AIO_PASS}"
echo "Szerverpult bejelentkezés: admin / ${PULT_PASS}"
echo "Most írd be a .env-be a TAILNET és TS_AUTHKEY értékét:  nano .env"
