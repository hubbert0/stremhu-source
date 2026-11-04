#!/usr/bin/env python3
"""Stremio → Nuvio átköltöztetés: kiegészítők, könyvtár, megnézett és
félbehagyott tételek egy Nuvio-fiók profiljába.

Futtatás a szerveren:
    python3 ~/stremhu-source/deploy/scripts/stremio_nuvio.py
    python3 ~/stremhu-source/deploy/scripts/stremio_nuvio.py --proba   # csak megmutatja

A belépési adatokat itt kéri be (a jelszó gépelés közben nem látszik), és csak
a Stremio és a Nuvio saját szerverének küldi el; sehova nem menti. A
Stremio-fiókban semmit nem változtat.

A Nuvio-oldali mezők ugyanúgy töltődnek ki, mint a Nuvio Account Manager
„Migrate from Stremio” funkciójában (github.com/techuhak/Nuvio-Account-Manager).
A kiegészítők sorrendje a Stremióé, de az Emby-könyvtár (a Mesék sorokkal)
kerül az élre, így a Nuvio kezdőlapján a Mesék sorok vannak felül.
"""

import argparse
import datetime
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

STREMIO_API = "https://api.strem.io"
NUVIO_API = "https://api.nuvio.tv"
# A Nuvio-alkalmazások nyilvános („anon”) kulcsa – nem titok, minden Nuvio-
# alkalmazásban benne van; a belépéshez a fiók e-mail-címe és jelszava kell.
NUVIO_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNl"
    "IiwiaWF0IjoxNzgxNTIxMzQ2LCJleHAiOjE5MzkyMDEzNDZ9.tmQaj682pwzehpqlgCDMnySOqiUvp"
    "gRbrE43T4VJpDI"
)
EMBY_ADDON_ID = "hu.stremhu.emby"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
LIBRARY_TYPES = {"movie", "series"}
CHUNK = 500
USER_AGENT = "stremhu-stremio-nuvio"


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------- HTTP


def http_json(
    url: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60,
) -> Any:
    """JSON-kérés. A hibaüzenetbe nem kerül sem a kérés, sem a jelszó."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data)
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8"))
            if isinstance(payload, dict):
                detail = str(
                    payload.get("msg")
                    or payload.get("error_description")
                    or payload.get("message")
                    or payload.get("error")
                    or ""
                )
        except (ValueError, OSError):
            pass
        raise ApiError(
            f"HTTP {error.code}" + (f" – {detail}" if detail else ""), error.code
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ApiError(f"nem érhető el ({type(error).__name__})") from None
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        raise ApiError("érvénytelen válasz") from None


# ------------------------------------------------------------------ Stremio


def stremio_call(path: str, body: dict[str, Any]) -> Any:
    data = http_json(f"{STREMIO_API}{path}", body)
    if not isinstance(data, dict):
        raise ApiError("váratlan válasz a Stremiótól")
    error = data.get("error") or data.get("err")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise ApiError(str(message or "ismeretlen hiba"))
    return data.get("result")


def stremio_login(email: str, password: str) -> str:
    result = stremio_call(
        "/api/login", {"email": email, "password": password, "facebook": False}
    )
    if not isinstance(result, dict) or not result.get("authKey"):
        raise ApiError("a Stremio nem adott belépési kulcsot")
    return str(result["authKey"])


def stremio_addons(auth_key: str) -> list[dict[str, Any]]:
    result = stremio_call(
        "/api/addonCollectionGet", {"authKey": auth_key, "update": True}
    )
    addons = result.get("addons") if isinstance(result, dict) else None
    return [a for a in addons or [] if isinstance(a, dict)]


def stremio_library(auth_key: str) -> list[dict[str, Any]]:
    result = stremio_call(
        "/api/datastoreGet",
        {"authKey": auth_key, "collection": "libraryItem", "ids": [], "all": True},
    )
    return [item for item in result or [] if isinstance(item, dict)]


# -------------------------------------------------------------------- Nuvio


def nuvio_headers(token: str | None = None) -> dict[str, str]:
    headers = {"apikey": NUVIO_ANON_KEY}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def nuvio_sign_in(email: str, password: str) -> str:
    data = http_json(
        f"{NUVIO_API}/auth/v1/token?grant_type=password",
        {"email": email, "password": password},
        nuvio_headers(),
    )
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ApiError("a Nuvio nem adott belépési kulcsot")
    return str(data["access_token"])


def nuvio_sign_up(email: str, password: str) -> str | None:
    """Új fiók. None, ha előbb az e-mailben kapott linkkel meg kell erősíteni."""
    data = http_json(
        f"{NUVIO_API}/auth/v1/signup",
        {"email": email, "password": password},
        nuvio_headers(),
    )
    if isinstance(data, dict) and data.get("access_token"):
        return str(data["access_token"])
    return None


def nuvio_rpc(token: str, name: str, body: dict[str, Any]) -> Any:
    return http_json(f"{NUVIO_API}/rest/v1/rpc/{name}", body, nuvio_headers(token))


def nuvio_addons(token: str, profile: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {"select": "*", "profile_id": f"eq.{profile}", "order": "sort_order"}
    )
    data = http_json(f"{NUVIO_API}/rest/v1/addons?{query}", None, nuvio_headers(token))
    return [a for a in data or [] if isinstance(a, dict)]


def nuvio_library_count(token: str, profile: int) -> int:
    rows = nuvio_rpc(
        token,
        "sync_pull_library",
        {"p_profile_id": profile, "p_limit": 500, "p_offset": 0},
    )
    return len(rows or [])


# ---------------------------------------------------------------- leképezés


def to_ms(value: Any, fallback: Any = None) -> int:
    for candidate in (value, fallback):
        if isinstance(candidate, str) and candidate:
            try:
                parsed = datetime.datetime.fromisoformat(candidate)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.UTC)
            return int(parsed.timestamp() * 1000)
    return int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)


def season_episode(video_id: str) -> tuple[int | None, int | None]:
    parts = video_id.split(":")
    if len(parts) != 3:
        return None, None

    def number(text: str) -> int | None:
        try:
            value = int(text)
        except ValueError:
            return None
        return value or None

    return number(parts[1]), number(parts[2])


def skip_reason(addon: dict[str, Any]) -> str | None:
    url = str(addon.get("transportUrl") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "nem webes cím"
    if parsed.hostname in LOCAL_HOSTS:
        return "csak az adott gépen működik"
    return None


def addon_name(addon: dict[str, Any]) -> str:
    manifest = addon.get("manifest") if isinstance(addon.get("manifest"), dict) else {}
    return str(manifest.get("name") or addon.get("transportUrl") or "?")


def plan_addons(
    stremio: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """A Nuvio-profil új kiegészítőlistája: a Stremio sorrendje, az
    Emby-könyvtárral az élen, utána a Nuvióban már meglévő többi."""
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for addon in stremio:
        url = str(addon.get("transportUrl") or "")
        if skip_reason(addon) or url in seen:
            continue
        seen.add(url)
        manifest = (
            addon.get("manifest") if isinstance(addon.get("manifest"), dict) else {}
        )
        chosen.append(
            {
                "url": url,
                "name": manifest.get("name"),
                "enabled": True,
                "emby": manifest.get("id") == EMBY_ADDON_ID,
            }
        )
    chosen.sort(key=lambda entry: not entry["emby"])  # stabil: a többi sorrendje marad
    for addon in existing:
        url = str(addon.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            chosen.append(
                {
                    "url": url,
                    "name": addon.get("name"),
                    "enabled": bool(addon.get("enabled", True)),
                    "emby": False,
                }
            )
    return [
        {
            "url": entry["url"],
            "name": entry["name"],
            "enabled": entry["enabled"],
            "sort_order": index,
        }
        for index, entry in enumerate(chosen)
    ]


def library_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "content_id": item["_id"],
            "content_type": item.get("type"),
            "name": item.get("name") or item["_id"],
            "poster": item.get("poster"),
            "poster_shape": "POSTER",
            "background": None,
            "description": None,
            "release_info": "",
            "imdb_rating": None,
            "genres": [],
            "addon_base_url": None,
            "added_at": to_ms(item.get("_ctime")),
        }
        for item in items
        if item.get("_id")
        and not item.get("temp")
        and not item.get("removed")
        and item.get("type") in LIBRARY_TYPES
    ]


def watch_history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for item in items:
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        if not item.get("_id") or item.get("temp"):
            continue
        if (state.get("timesWatched") or 0) <= 0:
            continue
        season, episode = season_episode(str(state.get("video_id") or ""))
        entries.append(
            {
                "content_id": item["_id"],
                "content_type": item.get("type"),
                "title": item["_id"],
                "season": season,
                "episode": episode,
                "watched_at": to_ms(state.get("lastWatched"), item.get("_mtime")),
            }
        )
    return entries


def watch_progress(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for item in items:
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        if not item.get("_id") or item.get("temp"):
            continue
        if (state.get("timeOffset") or 0) <= 0 or state.get("watched"):
            continue
        video_id = str(state.get("video_id") or item["_id"])
        season, episode = season_episode(video_id)
        key = (
            f"{item['_id']}_s{season}e{episode}"
            if season is not None and episode is not None
            else item["_id"]
        )
        entries.append(
            {
                "content_id": item["_id"],
                "content_type": item.get("type"),
                "video_id": video_id,
                "season": season,
                "episode": episode,
                # A Stremio másodpercben, a Nuvio ezredmásodpercben tárolja.
                "position": int((state.get("timeOffset") or 0) * 1000),
                "duration": int((state.get("duration") or 0) * 1000),
                "last_watched": to_ms(state.get("lastWatched"), item.get("_mtime")),
                "progress_key": key,
            }
        )
    return entries


# --------------------------------------------------------------------- főág


def ask(prompt: str) -> str:
    return input(prompt).strip()


def confirm(prompt: str) -> bool:
    return ask(prompt + " (i/n): ").lower() in ("i", "igen", "y", "yes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stremio → Nuvio átköltöztetés")
    parser.add_argument(
        "--proba", action="store_true", help="csak megmutatja, mit vinne át"
    )
    parser.add_argument(
        "--profil", type=int, default=1, help="Nuvio-profil sorszáma (alap: 1)"
    )
    args = parser.parse_args(argv)

    print("Stremio → Nuvio átköltöztetés")
    print("A jelszó gépelés közben nem látszik – ez így rendben van.\n")

    print("1/2 – Stremio-fiók (amiből másolunk; nem változik semmi benne)")
    try:
        auth_key = stremio_login(ask("  E-mail: "), getpass.getpass("  Jelszó: "))
        addons = stremio_addons(auth_key)
        library = stremio_library(auth_key)
    except ApiError as error:
        print(f"\nHIBA: a Stremio-belépés vagy a letöltés nem sikerült: {error}")
        return 1

    print(f"\n  Kiegészítők a Stremióban ({len(addons)}):")
    for addon in addons:
        reason = skip_reason(addon)
        mark = "✗" if reason else "✓"
        print(
            f"    {mark} {addon_name(addon)}"
            + (f"  (kimarad: {reason})" if reason else "")
        )
    lib = library_items(library)
    history = watch_history(library)
    progress = watch_progress(library)
    print(
        f"  Könyvtár: {len(lib)} tétel, megnézve: {len(history)}, "
        f"félbehagyva: {len(progress)}"
    )

    print("\n2/2 – Nuvio-fiók (ahová másolunk)")
    email = ask("  E-mail: ")
    password = getpass.getpass("  Jelszó: ")
    try:
        token = nuvio_sign_in(email, password)
    except ApiError as error:
        if error.status != 400:
            print(f"\nHIBA: a Nuvio-belépés nem sikerült: {error}")
            return 1
        if "confirm" in str(error).lower():
            print(
                "\n  A Nuvio-fiók még nincs megerősítve: kattints a Nuvio e-mailjében "
                "lévő linkre, majd futtasd újra ezt a parancsot."
            )
            return 1
        print("\n  Ezzel az e-mail-címmel és jelszóval nem sikerült belépni.")
        if not confirm(
            "  Nincs még Nuvio-fiókod? Létrehozzam ezzel az e-mail-címmel és jelszóval?"
        ):
            print(
                "Kilépek. Ellenőrizd az e-mail-címet és a jelszót, majd futtasd újra."
            )
            return 1
        try:
            token_or_none = nuvio_sign_up(email, password)
        except ApiError as signup_error:
            print(f"\nHIBA: a fiók létrehozása nem sikerült: {signup_error}")
            return 1
        if token_or_none is None:
            print(
                "\n  Ha ez új fiók: a Nuvio küldött egy megerősítő e-mailt – kattints "
                "benne a linkre, majd futtasd újra ezt a parancsot.\n"
                "  Ha már volt Nuvio-fiókod ezzel a címmel, akkor a jelszó volt rossz."
            )
            return 0
        token = token_or_none

    try:
        existing = nuvio_addons(token, args.profil)
        existing_library = nuvio_library_count(token, args.profil)
    except ApiError as error:
        print(f"\nHIBA: a Nuvio-profil nem olvasható: {error}")
        return 1

    plan = plan_addons(addons, existing)
    print(
        f"\n  A Nuvio {args.profil}. profiljának kiegészítői ebben a sorrendben lesznek:"
    )
    names = {str(a.get("transportUrl")): addon_name(a) for a in addons}
    for entry in plan:
        name = entry["name"] or names.get(entry["url"]) or entry["url"]
        print(f"    {entry['sort_order'] + 1}. {name}")
    if existing_library:
        print(
            f"  Figyelem: a Nuvio-könyvtárban lévő {existing_library} tétel helyére "
            "a Stremio-könyvtár kerül."
        )

    if args.proba:
        print("\nPróbaüzem: nem változtattam semmit.")
        return 0
    if not confirm("\nMehet az átmásolás?"):
        print("Rendben, nem változtattam semmit.")
        return 0

    failures = 0

    def step(label: str, work: Any) -> None:
        nonlocal failures
        try:
            work()
            print(f"  ✓ {label}")
        except ApiError as error:
            failures += 1
            print(f"  ✗ {label}: {error}")

    def chunks(rpc: str, key: str, entries: list[dict[str, Any]]) -> None:
        for start in range(0, len(entries), CHUNK):
            nuvio_rpc(
                token,
                rpc,
                {"p_profile_id": args.profil, key: entries[start : start + CHUNK]},
            )

    print()
    step(
        f"Kiegészítők ({len(plan)})",
        lambda: nuvio_rpc(
            token, "sync_push_addons", {"p_profile_id": args.profil, "p_addons": plan}
        ),
    )
    step(
        f"Könyvtár ({len(lib)})",
        lambda: nuvio_rpc(
            token, "sync_push_library", {"p_profile_id": args.profil, "p_items": lib}
        ),
    )
    if history:
        step(
            f"Megnézett tételek ({len(history)})",
            lambda: chunks("sync_push_watched_items", "p_items", history),
        )
    if progress:
        step(
            f"Folytatható tételek ({len(progress)})",
            lambda: chunks("sync_push_watch_progress", "p_entries", progress),
        )

    if failures:
        print(
            "\nNéhány rész nem sikerült (lásd fent). A parancs nyugodtan újrafuttatható."
        )
        return 1
    print(
        "\nKész. A Nuvio alkalmazásban jelentkezz be ezzel a Nuvio-fiókkal; "
        "a kiegészítők, a könyvtár és a megnézett tételek pár másodperc múlva megjelennek."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nMegszakítva.")
        sys.exit(130)
