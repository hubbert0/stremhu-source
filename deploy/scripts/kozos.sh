# Közös részek az önjavító figyelőhöz (onjavito.sh) és a heti frissítéshez
# (frissites.sh). POSIX sh; a hívó szkript ". kozos.sh"-sal tölti be.
# shellcheck shell=sh

PATH="${PATH:+${PATH}:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH

SCRIPTS_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
DEPLOY_DIR=$(CDPATH='' cd -- "${SCRIPTS_DIR}/.." && pwd)
ENV_FILE="${DEPLOY_DIR}/.env"
STATE_DIR="${STREMHU_STATE_DIR:-/var/lib/stremhu-karbantartas}"
LOG_FILE="${STREMHU_LOG_FILE:-/var/log/stremhu-karbantartas.log}"
LOCK_FILE="${STATE_DIR}/zar"
NTFY_URL="${STREMHU_NTFY_URL:-https://ntfy.sh}"

# A figyelt nyilvános címek:
# név|Tailscale-konténer|program|gépnév a .env-ben|alapértelmezett gépnév|útvonal
# shellcheck disable=SC2034 # a hívó szkriptek használják
SERVICES='StremHU|ts-stremhu|stremhu|STREMHU_HOSTNAME|stremhu|/
AIOStreams|ts-aiostreams|aiostreams|AIOSTREAMS_HOSTNAME|aiostreams|/api/v1/health
TMDB-addon|ts-tmdb|tmdb|TMDB_HOSTNAME|tmdb|/manifest.json
LeLibrary|ts-lelibrary|lelibrary|LELIBRARY_HOSTNAME|lelibrary|/health
Magyar feliratok|ts-felirat|feliratok|FELIRAT_HOSTNAME|felirat|/health
Emby-könyvtár|ts-emby|emby|EMBY_HOSTNAME|emby|/health
YouTube-mesék|ts-youtube|youtube|YOUTUBE_HOSTNAME|youtube|/health'

mkdir -p "${STATE_DIR}"

# Egy érték a .env-ből (idézőjelek nélkül); ha nincs vagy üres: a második paraméter.
env_get() {
  _v=$(sed -n "s/^$1=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1 | tr -d '\r')
  _v=${_v#\"}
  _v=${_v%\"}
  _v=${_v#\'}
  _v=${_v%\'}
  if [ -n "${_v}" ]; then
    printf '%s' "${_v}"
  else
    printf '%s' "${2:-}"
  fi
}

# Naplózás időbélyeggel; a napló legfeljebb kb. 2000 sor.
log() {
  mkdir -p "$(dirname -- "${LOG_FILE}")" 2>/dev/null || true
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "${LOG_FILE}"
  _n=$(wc -l < "${LOG_FILE}" | tr -d ' ')
  if [ "${_n:-0}" -gt 2000 ]; then
    tail -n 1000 "${LOG_FILE}" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "${LOG_FILE}"
  fi
}

# Szöveg JSON-karakterlánccá alakítása (vezérlőkarakterek nélkül).
json_escape() {
  printf '%s' "$1" | tr '\t' ' ' | tr -d '\000-\010\013-\037' \
    | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
    | awk 'NR > 1 { printf "\\n" } { printf "%s", $0 }'
}

# Értesítés a telefonra az ntfy-on át (ugyanarra a témára, mint az állapotfigyelő).
# notify CÍM ÜZENET [CÍMKE] [PRIORITÁS 1-5]
notify() {
  _topic=$(env_get NTFY_TOPIC)
  if [ -z "${_topic}" ]; then
    log "Nincs NTFY_TOPIC a .env-ben, az értesítés elmarad: $1"
    return 0
  fi
  _json=$(printf '{"topic":"%s","title":"%s","message":"%s","tags":["%s"],"priority":%s}' \
    "$(json_escape "${_topic}")" "$(json_escape "$1")" "$(json_escape "$2")" \
    "$(json_escape "${3:-information_source}")" "${4:-3}")
  if ! curl -fsS --max-time 20 -H 'Content-Type: application/json' \
    -d "${_json}" "${NTFY_URL}" >/dev/null 2>&1 </dev/null; then
    log "Az értesítést nem sikerült elküldeni: $1"
  fi
}

# A szolgáltatás nyilvános címe: service_url GÉPNÉV-VÁLTOZÓ ALAPÉRTÉK ÚTVONAL
service_url() {
  printf 'https://%s.%s%s' "$(env_get "$1" "$2")" "$(env_get TAILNET)" "$3"
}

# HTTP-állapotkód kívülről nézve ("000" = nem jött válasz).
http_status() {
  curl -s -o /dev/null -w '%{http_code}' --max-time "${STREMHU_CHECK_TIMEOUT:-20}" \
    "$1" </dev/null 2>/dev/null || true
}

# Működik-e: bármilyen 2xx/3xx/4xx válasz azt jelenti, hogy a cím él.
is_up() {
  case "$1" in
    2??|3??|4??) return 0 ;;
    *) return 1 ;;
  esac
}

# Kizárás, hogy a figyelő ne indítson újra semmit frissítés közben.
# take_lock -n (ha foglalt, azonnal feladja) vagy take_lock -w MÁSODPERC
take_lock() {
  exec 9>"${LOCK_FILE}"
  flock "$@" 9
}

release_lock() {
  exec 9>&-
}

# Egy szolgáltatás két konténerének (Tailscale + program) újraindítása.
restart_pair() {
  (cd "${DEPLOY_DIR}" && docker compose up -d --no-deps --force-recreate "$1" "$2" </dev/null) \
    >> "${LOG_FILE}" 2>&1
}
