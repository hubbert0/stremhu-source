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

# Régebbi .env-ekből hiányzó sorok pótlása.
ensure_line() {
  if ! grep -q "^$1=" .env; then
    printf '\n%s=%s\n' "$1" "$2" >> .env
  fi
}
ensure_line TORBOX_API_KEY ""
ensure_line TMDB_HOSTNAME "tmdb"
ensure_line TMDB_API_KEY ""
ensure_line FANART_API_KEY ""
ensure_line PULT_HOSTNAME "pult"
ensure_line PULT_USER "admin"
ensure_line PULT_PASSWORD ""
ensure_line REPO_DIR "${DIR}"
sed -i "s|^REPO_DIR=.*|REPO_DIR=${DIR}|" .env

# A szerverpult jelszava: ha még nincs, generálunk egyet.
if grep -q '^PULT_PASSWORD=$' .env; then
  if command -v openssl >/dev/null 2>&1; then
    PULT_PASS="$(openssl rand -hex 10)"
  else
    PULT_PASS="$(od -An -N10 -tx1 /dev/urandom | tr -d ' \n')"
  fi
  sed -i "s|^PULT_PASSWORD=.*|PULT_PASSWORD=${PULT_PASS}|" .env
fi

# Üres kulcs esetén rákérdez (Enter = kihagyás).
ask_key() {
  if grep -q "^$1=$" .env; then
    printf '%s (Enter = kihagyás): ' "$2"
    read -r VALUE </dev/tty
    if [ -n "${VALUE}" ]; then
      sed -i "s|^$1=.*|$1=${VALUE}|" .env
    fi
  fi
}
ask_key TORBOX_API_KEY "TorBox API-kulcs a seed-takarításhoz"
ask_key TMDB_API_KEY "TMDB API-kulcs a saját TMDB-addonhoz"

echo "Konténerek letöltése és indítása..."
# Ha egy image nem tölthető le (pl. a pult), az "up" helyben építi meg.
docker compose pull || echo "Figyelem: nem minden image tölthető le, a hiányzókat helyben építem."
docker compose up -d
docker compose ps

# Ha egy Tailscale-konténer még nincs bejelentkezve, kiírjuk a belépő linket.
sleep 15
for TS in ts-stremhu ts-aiostreams ts-tmdb ts-pult; do
  STATUS="$(docker compose exec -T "${TS}" tailscale status 2>/dev/null || true)"
  if printf '%s' "${STATUS}" | grep -q 'Logged out\|NeedsLogin'; then
    echo
    echo "A(z) ${TS} még nincs bejelentkezve a Tailscale-be. Nyisd meg böngészőben:"
    printf '%s\n' "${STATUS}" | grep -o 'https://login.tailscale.com/[^ ]*' | head -1
  fi
done

TAILNET_NOW="$(grep '^TAILNET=' .env | cut -d= -f2)"
STREMHU_NOW="$(grep '^STREMHU_HOSTNAME=' .env | cut -d= -f2)"
AIO_NOW="$(grep '^AIOSTREAMS_HOSTNAME=' .env | cut -d= -f2)"
TMDB_NOW="$(grep '^TMDB_HOSTNAME=' .env | cut -d= -f2)"
PULT_NOW="$(grep '^PULT_HOSTNAME=' .env | cut -d= -f2)"
PULT_USER_NOW="$(grep '^PULT_USER=' .env | cut -d= -f2)"
PULT_PASS_NOW="$(grep '^PULT_PASSWORD=' .env | cut -d= -f2)"

echo
echo "Kész. Pár perc múlva elérhető:"
echo "  StremHU:    https://${STREMHU_NOW}.${TAILNET_NOW}"
echo "  AIOStreams: https://${AIO_NOW}.${TAILNET_NOW}/stremio/configure"
echo "  TMDB-addon: https://${TMDB_NOW}.${TAILNET_NOW}/configure"
echo "AIOStreams-jelszó (ha elfelejtenéd):  grep AIOSTREAMS_AUTH ${DIR}/deploy/.env"
echo
echo "Szerverpult (csak a tailnetedről):"
echo "  Kezelőfelület: https://${PULT_NOW}.${TAILNET_NOW}"
echo "  Terminál:      https://${PULT_NOW}.${TAILNET_NOW}:8443"
echo "  Belépés:       ${PULT_USER_NOW} / ${PULT_PASS_NOW}"
