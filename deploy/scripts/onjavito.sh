#!/bin/sh
# Önjavító figyelő – a cron 3 percenként futtatja (az install.sh állítja be).
#
# Kívülről, a Tailscale Funnelen át (ahogy a családtagok Stremiója is látja)
# megnézi az öt nyilvános címet. Ha egy cím 3 egymás utáni ellenőrzésen nem
# válaszol (kb. 10 perc), a hozzá tartozó két konténert (Tailscale + program)
# újraindítja, és értesítést küld a telefonra (ntfy).
#
# Óvintézkedések:
# - ugyanazt a szolgáltatást legfeljebb óránként egyszer indítja újra;
# - ha egyszerre mind az öt cím elérhetetlen, nem indít újra semmit (az
#   hálózati vagy Tailscale-hiba, amin az újraindítás nem segít);
# - a heti frissítés közben nem fut.
#
# Kikapcsolás: a .env-ben ONJAVITO=0. Napló: /var/log/stremhu-karbantartas.log
set -u
# shellcheck source=deploy/scripts/kozos.sh
. "$(dirname -- "$0")/kozos.sh"

[ "$(env_get ONJAVITO 1)" = "0" ] && exit 0
[ -n "$(env_get TAILNET)" ] || exit 0
take_lock -n || exit 0

FAIL_LIMIT="${STREMHU_FAIL_LIMIT:-3}"
COOLDOWN="${STREMHU_COOLDOWN:-3600}"
NOW=$(date +%s)

read_number() {
  _x=$(cat "$1" 2>/dev/null || true)
  case "${_x}" in
    ''|*[!0-9]*) echo 0 ;;
    *) echo "${_x}" ;;
  esac
}

# 1. Minden cím ellenőrzése.
RESULTS="${STATE_DIR}/eredmeny"
: > "${RESULTS}"
total=0
failed=0
while IFS='|' read -r name ts app hostvar hostdef path; do
  [ -n "${name}" ] || continue
  code=$(http_status "$(service_url "${hostvar}" "${hostdef}" "${path}")")
  total=$((total + 1))
  if is_up "${code}"; then
    printf 'ok|%s|%s|%s|%s\n' "${name}" "${ts}" "${app}" "${code}" >> "${RESULTS}"
  else
    failed=$((failed + 1))
    printf 'hiba|%s|%s|%s|%s\n' "${name}" "${ts}" "${app}" "${code}" >> "${RESULTS}"
  fi
done <<EOF
${SERVICES}
EOF

if [ "${total}" -gt 0 ] && [ "${failed}" -eq "${total}" ]; then
  log "Egyik nyilvános cím sem válaszol – valószínűleg hálózati vagy Tailscale-hiba, nem indítok újra semmit."
  exit 0
fi

# 2. Kiértékelés szolgáltatásonként.
while IFS='|' read -r state name ts app code; do
  fails_file="${STATE_DIR}/${app}.hibak"
  if [ "${state}" = "ok" ]; then
    if [ -f "${fails_file}" ]; then
      rm -f "${fails_file}"
      log "${name} ismét válaszol."
    fi
    continue
  fi

  fails=$(( $(read_number "${fails_file}") + 1 ))
  echo "${fails}" > "${fails_file}"
  log "${name} nem válaszol (HTTP ${code}), ${fails}. egymás utáni hiba."
  [ "${fails}" -ge "${FAIL_LIMIT}" ] || continue

  last=$(read_number "${STATE_DIR}/${app}.ujrainditva")
  if [ $((NOW - last)) -lt "${COOLDOWN}" ]; then
    log "${name}: egy órán belül már újraindítottam, most nem próbálom újra."
    continue
  fi

  log "${name} újraindítása (${ts} + ${app})..."
  echo "${NOW}" > "${STATE_DIR}/${app}.ujrainditva"
  rm -f "${fails_file}"
  if restart_pair "${ts}" "${app}"; then
    log "${name} újraindítva."
    notify "${name} újraindítva" \
      "A(z) ${name} kb. ${fails} ellenőrzésen át (3 percenként) nem válaszolt kívülről, ezért az önjavító újraindította. Ha pár percen belül nem jön értesítés arról, hogy helyreállt, nézz rá az Állapotfigyelőben." \
      "arrows_counterclockwise" 4
  else
    log "${name} újraindítása nem sikerült."
    notify "${name}: az újraindítás nem sikerült" \
      "Az önjavító nem tudta újraindítani: ${name}. A részletek a szerveren: tail -n 50 ${LOG_FILE}" \
      "warning" 5
  fi
done < "${RESULTS}"
