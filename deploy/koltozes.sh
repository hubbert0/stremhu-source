#!/usr/bin/env bash
# Költözés új szerverre. Az ÚJ szerveren kell futtatni, rootként:
#
#   curl -fsSL https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/koltozes.sh -o koltozes.sh
#   bash koltozes.sh RÉGI-SZERVER-IP-CÍME
#
# Lépések:
#  1. Előkészíti az új szervert: Docker, a repó, a .env a régiről, az image-ek.
#     A régi rendszer közben még fut.
#  2. Leállítja a régi rendszert (a konténereket nem törli), és ott kikapcsolja
#     az automatikus karbantartást.
#  3. Átmásolja az összes adatkötetet (beállítások, adatbázisok, Tailscale-
#     azonosítók, széf, mentés-beállítás), és ellenőrzi, hogy hiánytalan-e.
#  4. Elindítja a rendszert az új szerveren, és kívülről ellenőrzi a címeket.
#  5. Átveszi a régi szerver SSH-gépkulcsát és -belépési kulcsait, így a saját
#     gépeden (PowerShell, Szerverpult.exe) a névvel semmit nem kell újra beállítani.
#
# A Tailscale-azonosítók átkerülnek, ezért minden cím ugyanaz marad: a család
# Stremiójában semmit nem kell átállítani.
# Ha a 2–3. lépésben hiba történik, a régi rendszert magától visszaindítja.
set -Eeuo pipefail

REPO_URL="${KOLTOZES_REPO_URL:-https://github.com/hubbert0/stremhu-source.git}"
BRANCH="torbox"
PROJECT="stremhu-torbox"
HELPER_IMAGE="debian:bookworm-slim"
NEW_REPO="${KOLTOZES_UJ_REPO:-${HOME}/stremhu-source}"
OLD_REPO="${KOLTOZES_REGI_REPO:-/root/stremhu-source}"
CRON_FILE="${KOLTOZES_CRON_FILE:-/etc/cron.d/stremhu-torbox}"
CRON_PARKED="${KOLTOZES_CRON_PARKED:-/root/stremhu-torbox.cron.kikapcsolva}"
CHECK_INTERVAL="${KOLTOZES_ELLENORZES_SEC:-15}"
ROUNDS_BEFORE_HEAL="${KOLTOZES_KOROK_JAVITAS_ELOTT:-12}"
ROUNDS_AFTER_HEAL="${KOLTOZES_KOROK_JAVITAS_UTAN:-8}"
SSH_CTL="/tmp/koltozes-ssh-$$"

OLD_HOST="${1:-}"
OLD_STOPPED=0
NEW_STARTED=0
DONE=0

say() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die() {
  printf '\nHIBA: %s\n' "$*" >&2
  exit 1
}

# Parancs futtatása a régi szerveren. Az első híváskor kéri a régi szerver
# root-jelszavát, utána ugyanazt a kapcsolatot használja.
old() {
  ssh -o ControlMaster=auto -o ControlPath="${SSH_CTL}" -o ControlPersist=20m \
    -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15 \
    "root@${OLD_HOST}" "$1"
}

# Biztonságosan idézett parancssor a távoli shellnek.
quote() { printf '%q ' "$@"; }

close_ssh() {
  ssh -o ControlPath="${SSH_CTL}" -O exit "root@${OLD_HOST:-x}" >/dev/null 2>&1 || true
}

on_exit() {
  local status=$?
  if [ "${DONE}" = "1" ] || [ "${status}" = "0" ]; then
    close_ssh
    return 0
  fi
  if [ "${OLD_STOPPED}" = "1" ] && [ "${NEW_STARTED}" = "0" ]; then
    say "A költözés megszakadt – a régi rendszert visszaindítom"
    if old "$(quote sh -c "if [ -f '${CRON_PARKED}' ]; then mv '${CRON_PARKED}' '${CRON_FILE}'; fi; cd '${OLD_REPO}/deploy' && docker compose start")"; then
      info "A régi szerver újra fut, ott minden a régiben maradt."
    else
      info "Nem sikerült visszaindítani. A régi szerveren futtasd:"
      info "  mv ${CRON_PARKED} ${CRON_FILE}; cd ${OLD_REPO}/deploy && docker compose start"
    fi
  elif [ "${NEW_STARTED}" = "1" ]; then
    printf '\nAz új szerveren már elindult a rendszer, ezért a régit nem indítom vissza\n'
    printf '(a kettő egyszerre ütközne a Tailscale-ben). Küldd el a fenti hibát.\n'
    printf 'Ha vissza kell állni a régire: az ÚJ szerveren  cd %s/deploy && docker compose down\n' "${NEW_REPO}"
    printf 'majd a RÉGIN  mv %s %s; cd %s/deploy && docker compose start\n' \
      "${CRON_PARKED}" "${CRON_FILE}" "${OLD_REPO}"
  fi
  close_ssh
}
trap on_exit EXIT

# ------------------------------------------------------------------ 0. Indulás

if [ "${KOLTOZES_ROOT_ELLENORZES:-1}" = "1" ] && [ "$(id -u)" != "0" ]; then
  die "rootként futtasd (a belépés: ssh root@...)."
fi
if [ -z "${OLD_HOST}" ]; then
  read -r -p "A RÉGI szerver IP-címe: " OLD_HOST </dev/tty
fi
[ -n "${OLD_HOST}" ] || die "add meg a régi szerver IP-címét: bash koltozes.sh 1.2.3.4"

say "Kapcsolódás a régi szerverhez (${OLD_HOST}) – írd be a RÉGI szerver root-jelszavát"
old "true" || die "nem sikerült belépni a régi szerverre."

if [ "${KOLTOZES_GEPAZONOSITO:-1}" = "1" ]; then
  if [ "$(old 'cat /etc/machine-id')" = "$(cat /etc/machine-id)" ]; then
    die "ez ugyanaz a szerver. A szkriptet az ÚJ szerveren kell futtatni, a RÉGI címével."
  fi
fi
old "$(quote test -f "${OLD_REPO}/deploy/.env")" \
  || die "a régi szerveren nincs ${OLD_REPO}/deploy/.env – biztos jó a cím?"
OLD_VOLUMES=$(old "$(quote docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT}")")
[ -n "${OLD_VOLUMES}" ] || die "a régi szerveren nem találom a rendszer adatköteteit."
DDNS_HINT=$(old 'grep -rls myaddr /etc/cron* /var/spool/cron 2>/dev/null || true')

# ------------------------------------------------- 1. Az új szerver előkészítése

say "Az új szerver előkészítése (a régi közben még fut)"
if [ "${KOLTOZES_IDOZONA:-1}" = "1" ] && command -v timedatectl >/dev/null 2>&1; then
  timedatectl set-timezone Europe/Budapest || true
fi
# Frissen telepített szerveren az Ubuntu az első percekben frissíti magát, és
# addig fogja a csomagkezelőt. Az apt ilyenkor várjon (legfeljebb 30 percet).
APT_WAIT_CONF="${KOLTOZES_APT_CONF:-/etc/apt/apt.conf.d/99koltozes-varakozas}"
if [ -d "$(dirname "${APT_WAIT_CONF}")" ]; then
  echo 'DPkg::Lock::Timeout "1800";' > "${APT_WAIT_CONF}" 2>/dev/null || true
fi
if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  info "git és curl telepítése..."
  apt-get update -q && apt-get install -y -q git curl
fi
if ! command -v docker >/dev/null 2>&1; then
  info "Docker telepítése (pár perc)..."
  curl -fsSL https://get.docker.com | sh
fi
rm -f "${APT_WAIT_CONF}"
docker compose version >/dev/null 2>&1 || die "a 'docker compose' nem érhető el."

if [ -d "${NEW_REPO}/.git" ]; then
  git -C "${NEW_REPO}" pull --ff-only -q
else
  git clone -q -b "${BRANCH}" "${REPO_URL}" "${NEW_REPO}"
fi
NEW_DEPLOY="${NEW_REPO}/deploy"

info "A .env átvétele a régi szerverről..."
[ -f "${NEW_DEPLOY}/.env" ] && cp -p "${NEW_DEPLOY}/.env" "${NEW_DEPLOY}/.env.elozo"
umask 077
old "$(quote cat "${OLD_REPO}/deploy/.env")" > "${NEW_DEPLOY}/.env.tmp"
umask 022
grep -q '^TAILNET=' "${NEW_DEPLOY}/.env.tmp" || die "a régi .env hiányos (nincs benne TAILNET)."
sed -i "s|^REPO_DIR=.*|REPO_DIR=${NEW_REPO}|" "${NEW_DEPLOY}/.env.tmp"
mv "${NEW_DEPLOY}/.env.tmp" "${NEW_DEPLOY}/.env"

info "Az image-ek letöltése és a konténerek létrehozása (még nem indulnak el)..."
(cd "${NEW_DEPLOY}" && docker compose pull -q) || info "Figyelem: nem minden image tölthető le, a hiányzókat helyben építem."
(cd "${NEW_DEPLOY}" && docker compose create)
docker pull -q "${HELPER_IMAGE}" >/dev/null
old "$(quote docker pull -q "${HELPER_IMAGE}")" >/dev/null

# ---------------------------------------------------------- 2. A régi leállítása

say "Most leáll a régi rendszer, és átköltözik (kb. 5 perc kiesés)"
if [ "${KOLTOZES_IGEN:-0}" != "1" ]; then
  read -r -p "Mehet? [i/n] " ANSWER </dev/tty
  case "${ANSWER}" in
    i|I|igen|Igen) ;;
    *) die "megszakítva, semmi nem változott a régi szerveren." ;;
  esac
fi
OLD_STOPPED=1
old "$(quote sh -c "if [ -f '${CRON_FILE}' ]; then mv '${CRON_FILE}' '${CRON_PARKED}'; fi; cd '${OLD_REPO}/deploy' && docker compose stop")"
info "A régi rendszer leállt (a konténerek megvannak, nem töröltem semmit)."

# ------------------------------------------------------- 3. Az adatok átmásolása

say "Az adatkötetek átmásolása"
# Fájlok száma és összmérete – ez alapján ellenőrzi, hogy minden átjött-e.
# shellcheck disable=SC2016 # a konténerben futó shell bontja ki
COUNT_SCRIPT='find /adat -type f -printf "%s\n" | awk "{ n++; s += \$1 } END { print n + 0, s + 0 }"'
for volume in ${OLD_VOLUMES}; do
  if ! docker volume inspect "${volume}" >/dev/null 2>&1; then
    docker volume create \
      --label "com.docker.compose.project=${PROJECT}" \
      --label "com.docker.compose.volume=${volume#"${PROJECT}"_}" \
      "${volume}" >/dev/null
  fi
  old "$(quote docker run --rm -v "${volume}:/adat:ro" "${HELPER_IMAGE}" \
    tar --numeric-owner -C /adat -cf - .)" \
    | docker run --rm -i -v "${volume}:/adat" "${HELPER_IMAGE}" \
      sh -c 'find /adat -mindepth 1 -delete && tar --numeric-owner -C /adat -xpf -'
  want=$(old "$(quote docker run --rm -v "${volume}:/adat:ro" "${HELPER_IMAGE}" sh -c "${COUNT_SCRIPT}")")
  got=$(docker run --rm -v "${volume}:/adat:ro" "${HELPER_IMAGE}" sh -c "${COUNT_SCRIPT}")
  [ "${want}" = "${got}" ] || die "a(z) ${volume} kötet nem jött át hiánytalanul (régi: ${want}, új: ${got})."
  info "${volume#"${PROJECT}"_}: ${got%% *} fájl rendben"
done

# ------------------------------------------------------------- 4. Indítás

say "Indítás az új szerveren"
NEW_STARTED=1
STREMHU_DIR="${NEW_REPO}" STREMHU_AUTO=1 sh "${NEW_DEPLOY}/install.sh"

env_get() { sed -n "s/^$1=//p" "${NEW_DEPLOY}/.env" | tail -n 1 | tr -d '\r"'"'"; }
TAILNET=$(env_get TAILNET)
SERVICES="StremHU|ts-stremhu|stremhu|$(env_get STREMHU_HOSTNAME)|/
AIOStreams|ts-aiostreams|aiostreams|$(env_get AIOSTREAMS_HOSTNAME)|/api/v1/health
TMDB-addon|ts-tmdb|tmdb|$(env_get TMDB_HOSTNAME)|/manifest.json
LeLibrary|ts-lelibrary|lelibrary|$(env_get LELIBRARY_HOSTNAME)|/health
Magyar feliratok|ts-felirat|feliratok|$(env_get FELIRAT_HOSTNAME)|/health
Emby-könyvtár|ts-emby|emby|$(env_get EMBY_HOSTNAME)|/health"

check_all() {
  FAILING=""
  local name ts app host path code
  while IFS='|' read -r name ts app host path; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://${host}.${TAILNET}${path}" || true)
    case "${code}" in
      2??|3??|4??) ;;
      *) FAILING="${FAILING}${name}|${ts}|${app}"$'\n' ;;
    esac
  done <<<"${SERVICES}"
  [ -z "${FAILING}" ]
}

say "A nyilvános címek ellenőrzése kívülről (legfeljebb kb. 5 perc)"
ok=0
for _ in $(seq "${ROUNDS_BEFORE_HEAL}"); do
  sleep "${CHECK_INTERVAL}"
  if check_all; then ok=1; break; fi
done
if [ "${ok}" = "0" ]; then
  info "Ami még nem válaszol, azt egyszer újraindítom:"
  while IFS='|' read -r name ts app; do
    [ -n "${name}" ] || continue
    info "  ${name}"
    (cd "${NEW_DEPLOY}" && docker compose up -d --no-deps --force-recreate "${ts}" "${app}") >/dev/null 2>&1 || true
  done <<<"${FAILING}"
  for _ in $(seq "${ROUNDS_AFTER_HEAL}"); do
    sleep "${CHECK_INTERVAL}"
    if check_all; then ok=1; break; fi
  done
fi

# ------------------------------------------------ 5. SSH-kulcsok átvétele

if [ "${KOLTOZES_SSH_KULCSOK:-1}" = "1" ]; then
  say "Az SSH-kulcsok átvétele a régi szerverről"
  mkdir -p "${HOME}/.ssh" && chmod 700 "${HOME}/.ssh"
  touch "${HOME}/.ssh/authorized_keys" && chmod 600 "${HOME}/.ssh/authorized_keys"
  while IFS= read -r key; do
    [ -n "${key}" ] || continue
    grep -qxF "${key}" "${HOME}/.ssh/authorized_keys" || printf '%s\n' "${key}" >> "${HOME}/.ssh/authorized_keys"
  done <<<"$(old 'cat ~/.ssh/authorized_keys 2>/dev/null || true')"
  info "Belépési kulcsok (pl. a Szerverpult.exe-é) átvéve."
  if old 'tar -C /etc/ssh -cf - ssh_host_ecdsa_key ssh_host_ecdsa_key.pub ssh_host_ed25519_key ssh_host_ed25519_key.pub ssh_host_rsa_key ssh_host_rsa_key.pub' > /tmp/koltozes-gepkulcs.tar; then
    tar -C /etc/ssh -xf /tmp/koltozes-gepkulcs.tar
    chmod 600 /etc/ssh/ssh_host_*_key
    chmod 644 /etc/ssh/ssh_host_*_key.pub
    systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
    info "Az SSH-gépkulcs átvéve: a saját gépeden a névvel (pl. xstrem...) nem lesz figyelmeztetés."
  else
    info "Figyelem: a gépkulcsot nem sikerült átvenni, a saját gépeden egyszer figyelmeztetni fog."
  fi
  rm -f /tmp/koltozes-gepkulcs.tar
fi

# --------------------------------------------------------------- Összegzés

DONE=1
NEW_IP=$( (ip -4 route get 1.1.1.1 2>/dev/null || true) | awk '{ for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1) }' | head -n 1)
[ -n "${NEW_IP}" ] || NEW_IP=$( (hostname -I 2>/dev/null || true) | awk '{ print $1 }')
say "Kész"
if [ "${ok}" = "1" ]; then
  info "Minden nyilvános cím működik az új szerverről."
else
  info "Még nem válaszol:"
  printf '%s' "${FAILING}" | while IFS='|' read -r name _ _; do [ -n "${name}" ] && info "  - ${name}"; done
  info "Az önjavító figyelő 10 percen belül újra megpróbálja. Ha nem jön rendbe, küldd el ezt a kimenetet."
fi
cat <<EOF

Teendők:
  1. A myaddr.io-n állítsd át a szervered nevét (pl. xstrem) az új IP-címre: ${NEW_IP:-az új szerver IP-címe}
  2. Utána a saját gépeden minden a régi névvel megy (PowerShell: ssh root@<név>, Szerverpult.exe).
     Ha az új IP-címmel lépnél be, és a PowerShell 'REMOTE HOST IDENTIFICATION HAS CHANGED'
     figyelmeztetést ír, ez várható (átvettük a régi gépkulcsot). Megoldás a saját gépeden:
       ssh-keygen -R ${NEW_IP:-AZ-ÚJ-IP}
  3. Nézz bele a Stremióba: keresés, lejátszás, LeLibrary.
  4. A régi szerveren a rendszer leállítva marad. NE indítsd el újra (a Tailscale-ben
     ütközne az újjal). Ha pár nap után minden rendben, mondd le a Contabónál.
EOF
if [ -n "${DDNS_HINT}" ]; then
  cat <<EOF

Figyelem: a régi szerveren van egy myaddr.io-frissítő (${DDNS_HINT}).
  Az a nevet a RÉGI IP-re állíthatja vissza – töröld ott, vagy állítsd be ugyanezt az újon.
EOF
fi
