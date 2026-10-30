# StremHU + TorBox – otthoni telepítés

Ez a csomag egy otthoni gépen (pl. ThinkPad T420 Linuxszal) futtatja:

| Szolgáltatás | Feladata | Kívülről elérhető? |
|---|---|---|
| **StremHU** (`torbox` ág, a #283-as PR-rel) | nCore-keresés, `.torrent` kiadása Torznabon | igen, Tailscale Funnel |
| **AIOStreams** | ezt adod a Stremióhoz; a találatokat TorBoxra küldi | igen, Tailscale Funnel |
| **StremThru** | a `.torrent` fájlt feltölti a TorBoxra | nem, csak belső |

```
Stremio → AIOStreams → StremHU (keresés, .torrent) → StremThru → TorBox
                                                                  ↓
Stremio ←──────────────── lejátszás közvetlenül a TorBoxról ───────┘
```

A filmek **nem** mennek át ezen a gépen, és nem is tárolódnak rajta.

## Amit érdemes tudni előre

- **TorBox:** privát torrenthez fizetős csomag kell (az ingyenes egyáltalán nem tölt privát torrentet). Az nCore seedelési szabályai miatt a **Standard** csomag a biztonságos.
- **nCore-kockázat:** a TorBox a te passkey-eddel, a saját IP-iről tölt és seedel. Az nCore nem szerepel a TorBox által tesztelt trackerek között – ez a te döntésed.
- **Nem cache-elt tartalom:** ha a torrent még nincs a TorBoxon, az első indításnál meg kell várni, amíg a TorBox letölti (addig egy „próbáld később” videót kapsz).
- **Kísérleti:** a #283-as PR még nincs a hivatalos StremHU-ban.

## 1. Előkészületek

1. **Tailscale auth key:** [Tailscale admin → Settings → Keys](https://login.tailscale.com/admin/settings/keys) → *Generate auth key* → **Reusable** bekapcsolva → másold ki.
2. **Tailnet neve:** Tailscale admin → *DNS* oldal, pl. `valami-nev.ts.net`.
3. **TorBox API-kulcs:** TorBox → Settings → API key.

A Funnelnek engedélyezve kell lennie a tailneten – mivel már használsz Funnelt, ez valószínűleg rendben van.

## 2. Docker telepítése (Ubuntu/Debian)

```sh
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Utána jelentkezz ki és be (vagy indítsd újra a gépet).

## 3. Letöltés és beállítás

```sh
git clone -b torbox https://github.com/hubbert0/stremhu-source.git
cd stremhu-source/deploy
sh init-env.sh
```

Az `init-env.sh` létrehozza a `.env` fájlt, kitölti a titkos kulcsokat, és **kiírja az AIOStreams-jelszót – jegyezd fel.**

Ezután szerkeszd a `.env`-et, és írd be a `TAILNET` és `TS_AUTHKEY` értékét:

```sh
nano .env
```

(Mentés: `Ctrl+O`, Enter; kilépés: `Ctrl+X`.)

## 4. Indítás

```sh
docker compose up -d
docker compose ps
```

Mind az öt konténernek `running` állapotúnak kell lennie. Pár perc után elérhető:

- `https://stremhu-tb.<tailnet>` – StremHU
- `https://aiostreams.<tailnet>` – AIOStreams

> **Ha a letöltésnél `denied` / `unauthorized` hibát kapsz** a `ghcr.io/hubbert0/stremhu-source` image-re: GitHubon a profilodnál → *Packages* → `stremhu-source` → *Package settings* → *Change visibility* → **Public**. (A kód amúgy is nyilvános, az image nem tartalmaz titkot.)

## 5. StremHU beállítása

1. Nyisd meg: `https://stremhu-tb.<tailnet>`
2. Hozd létre az admin fiókot, és add meg az nCore-belépést.
3. A főoldalon, a **Támogatott kliensek** résznél van a **Torznab** kártya: másold ki a **Torznab URL**-t és az **API kulcs**ot.

## 6. AIOStreams beállítása

1. Nyisd meg: `https://aiostreams.<tailnet>/stremio/configure`, és lépj be (`admin` + a feljegyzett jelszó).
2. **Services:** kapcsold be a **TorBox**-ot, és add meg a TorBox API-kulcsot.
3. **Addons:** add hozzá a **Torznab** beépített addont:
   - *Name:* `StremHU`
   - *Torznab URL:* a StremHU-ból kimásolt URL (`https://stremhu-tb.<tailnet>/api/torznab`)
   - *API Key:* a StremHU API kulcsa
   - *Services:* TorBox
   - *Search Mode:* **Auto** (a StremHU csak IMDb-azonosítóval keres)
4. Nemzetközi tartalomhoz ugyanitt bekapcsolhatsz más addont is (pl. Torrentio vagy TorBox Search) a TorBox szolgáltatással.
5. A beépített **proxyt hagyd kikapcsolva**, különben a stream ezen a gépen menne át.
6. Mentés, majd **Install** → megnyílik a Stremio.

A menünevek AIOStreams-verziónként kicsit eltérhetnek.

## 7. Próba

Keress rá Stremióban egy filmre, ami biztosan fent van nCore-on. A `StremHU` találatok között lesznek azonnal indíthatók (TorBox-cache) és letöltendők. Egy nem cache-elt találat indításakor a TorBox felületén látnod kell a letöltést.

Ha nincs magyar találat, ellenőrizd böngészőben (a kulcsot cseréld a sajátodra):

```
https://stremhu-tb.<tailnet>/api/torznab/api?t=caps&apikey=API_KULCS
```

Ha XML jön vissza, a StremHU rendben van, és az AIOStreams-beállításban lesz a hiba.

## Frissítés

```sh
cd ~/stremhu-source/deploy
docker compose pull
docker compose up -d
```

A `torbox` ágra került minden módosítás után a GitHub automatikusan új image-et épít; a fenti parancs ezt húzza le.

**Ha a hivatalos StremHU beolvasztja a #283-as PR-t,** a `.env`-ben a `STREMHU_IMAGE` sor átírható `s4pp1/stremhu-source:latest`-re, és a fork feleslegessé válik.

## Hibakeresés

```sh
docker compose logs --tail 100 stremhu
docker compose logs --tail 100 aiostreams
docker compose logs --tail 100 stremthru
docker compose logs --tail 100 ts-stremhu
```

## Windowson (Docker Desktop)

Ugyanígy működik: Git Bash-ben futtasd a fenti parancsokat. Ha nincs Git Bash, másold `.env.example`-t `.env`-re, és a titkokhoz ezt a PowerShell-parancsot használd (kétszer, két különböző értékhez):

```powershell
-join ((0..31) | ForEach-Object { '{0:x2}' -f (Get-Random -Minimum 0 -Maximum 255) })
```

A gépnek ilyenkor is folyamatosan bekapcsolva kell lennie.
