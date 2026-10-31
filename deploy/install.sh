#!/usr/bin/env sh
# Egylépéses telepítő Linux-szerverre (pl. Contabo VPS, Ubuntu + Docker).
#
# Használat a szerveren:
#   curl -fsSL https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/install.sh -o install.sh
#   sh install.sh
#
# Letölti/frissíti a repót, létrehozza a .env-et a titkokkal, bekéri a tailnet
# nevét és a Tailscale auth key-t, majd elindítja a konténereket.
# Többször is futtatható: a meglévő .env-et és beállításokat nem írja felül.
set -eu

REPO_URL="https://github.com/hubbert0/stremhu-source.git"
BRANCH="torbox"
DIR="${HOME}/stremhu-source"

if ! command -v docker >/dev/null 2>&1; then
  echo "Hiba: a Docker nincs telepítve. Telepítés: curl -fsSL https://get.docker.com | sh"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Hiba: a 'docker compose' nem érhető el. Telepítsd a docker-compose-plugin csomagot."
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "Hiba: a git nincs telepítve. Telepítés: apt-get install -y git"
  exit 1
fi

if [ -d "${DIR}/.git" ]; then
  echo "A repó már megvan, frissítem: ${DIR}"
  git -C "${DIR}" pull --ff-only
else
  git clone -b "${BRANCH}" "${REPO_URL}" "${DIR}"
fi

cd "${DIR}/deploy"
sh init-env.sh

if grep -q '^TAILNET=valami-nev\.ts\.net' .env; then
  printf 'Tailnet neve (Tailscale admin -> DNS, pl. valami-nev.ts.net): '
  read -r TAILNET_VALUE </dev/tty
  sed -i "s|^TAILNET=.*|TAILNET=${TAILNET_VALUE}|" .env
fi

if grep -q '^TS_AUTHKEY=tskey-auth-XXXXXXXXXXXX' .env; then
  printf 'Tailscale auth key (tskey-auth-...): '
  read -r TS_KEY_VALUE </dev/tty
  sed -i "s|^TS_AUTHKEY=.*|TS_AUTHKEY=${TS_KEY_VALUE}|" .env
fi

echo "Konténerek letöltése és indítása..."
docker compose pull
docker compose up -d
docker compose ps

TAILNET_NOW="$(grep '^TAILNET=' .env | cut -d= -f2)"
STREMHU_NOW="$(grep '^STREMHU_HOSTNAME=' .env | cut -d= -f2)"
AIO_NOW="$(grep '^AIOSTREAMS_HOSTNAME=' .env | cut -d= -f2)"

echo
echo "Kész. Pár perc múlva elérhető:"
echo "  StremHU:    https://${STREMHU_NOW}.${TAILNET_NOW}"
echo "  AIOStreams: https://${AIO_NOW}.${TAILNET_NOW}/stremio/configure"
echo "AIOStreams-jelszó (ha elfelejtenéd):  grep AIOSTREAMS_AUTH ${DIR}/deploy/.env"
