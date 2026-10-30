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

chmod 600 .env

echo "Kész: .env létrehozva."
echo "AIOStreams bejelentkezés:  admin / ${AIO_PASS}"
echo "Most írd be a .env-be a TAILNET és TS_AUTHKEY értékét:  nano .env"
