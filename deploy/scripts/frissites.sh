#!/bin/sh
# Heti automatikus frissítés – a cron vasárnap hajnali 5-kor futtatja
# (a 4 órás mentés után; az install.sh állítja be).
#
# 1. Letölti a repó új verzióját (git pull).
# 2. Lefuttatja a telepítőt felügyelet nélküli módban: pótolja a .env új
#    sorait, letölti az új image-eket, és újraindítja, ami változott.
# 3. 3 perc múlva kívülről ellenőrzi a nyilvános címeket.
# 4. Az eredményről értesítést küld a telefonra (ntfy).
#
# Kikapcsolás: a .env-ben AUTO_FRISSITES=0. Napló: /var/log/stremhu-karbantartas.log
set -u
# shellcheck source=deploy/scripts/kozos.sh
. "$(dirname -- "$0")/kozos.sh"

[ "$(env_get AUTO_FRISSITES 1)" = "0" ] && exit 0
if ! take_lock -w 600; then
  log "Heti frissítés: egy másik karbantartás fut, most kimarad."
  exit 1
fi

REPO_DIR=$(CDPATH='' cd -- "${DEPLOY_DIR}/.." && pwd)
log "Heti frissítés indul."

fail() {
  log "Heti frissítés: hiba – $1"
  notify "Heti frissítés: hiba" "$1" "warning" 5
  exit 1
}

BEFORE="${STATE_DIR}/kontenerek.elotte"
(cd "${DEPLOY_DIR}" && docker compose ps -q 2>/dev/null </dev/null | sort) > "${BEFORE}"
OLD=$(git -C "${REPO_DIR}" rev-parse --short HEAD 2>/dev/null || echo "?")

if ! OUT=$(git -C "${REPO_DIR}" pull --ff-only 2>&1 </dev/null); then
  printf '%s\n' "${OUT}" >> "${LOG_FILE}"
  fail "A repó frissítése (git pull) nem sikerült, semmi nem változott. A szerveren: cd ${REPO_DIR} && git status"
fi
NEW=$(git -C "${REPO_DIR}" rev-parse --short HEAD 2>/dev/null || echo "?")

# A frissített telepítő, kérdések nélkül.
if ! OUT=$(STREMHU_DIR="${REPO_DIR}" STREMHU_AUTO=1 sh "${DEPLOY_DIR}/install.sh" 2>&1 </dev/null); then
  printf '%s\n' "${OUT}" >> "${LOG_FILE}"
  fail "A telepítő hibával állt le. Utolsó sorok:
$(printf '%s\n' "${OUT}" | tail -n 8)"
fi
printf '%s\n' "${OUT}" >> "${LOG_FILE}"

AFTER="${STATE_DIR}/kontenerek.utana"
(cd "${DEPLOY_DIR}" && docker compose ps -q 2>/dev/null </dev/null | sort) > "${AFTER}"
if [ -s "${BEFORE}" ]; then
  RESTARTED=$(grep -cvxF -f "${BEFORE}" "${AFTER}" || true)
else
  RESTARTED=$(grep -c . "${AFTER}" || true)
fi
docker image prune -f >/dev/null 2>&1 </dev/null || true

# Az ellenőrzés idejére az önjavító is futhat.
release_lock
sleep "${STREMHU_UPDATE_WAIT:-180}"

BAD=""
while IFS='|' read -r name _ _ hostvar hostdef path; do
  [ -n "${name}" ] || continue
  code=$(http_status "$(service_url "${hostvar}" "${hostdef}" "${path}")")
  is_up "${code}" || BAD="${BAD:+${BAD}, }${name}"
done <<EOF
${SERVICES}
EOF

if [ "${OLD}" = "${NEW}" ]; then
  CODE_LINE="A program kódja nem változott."
else
  CODE_LINE="A program kódja frissült (${OLD} → ${NEW})."
fi
if [ -z "${BAD}" ]; then
  STATUS_LINE="Minden nyilvános cím működik."
  log "Heti frissítés kész: ${RESTARTED} konténer frissült, minden cím működik."
  notify "Heti frissítés kész" \
    "${CODE_LINE}
Frissített konténerek: ${RESTARTED}.
${STATUS_LINE}" "white_check_mark" 2
else
  STATUS_LINE="Most nem válaszol: ${BAD}. Az önjavító kb. 10 percen belül újraindítja."
  log "Heti frissítés kész: ${RESTARTED} konténer frissült, nem válaszol: ${BAD}."
  notify "Heti frissítés kész, de valami nem válaszol" \
    "${CODE_LINE}
Frissített konténerek: ${RESTARTED}.
${STATUS_LINE}" "warning" 4
fi
