# StremHU + TorBox – dokumentáció

Ez a leírás végigvezet a rendszer telepítésén, beállításán és mindennapi kezelésén. Linux-tudás nem kell hozzá: minden parancs szó szerint bemásolható.

## Tartalom

1. [Áttekintés](#1-áttekintés)
2. [Fogalmak](#2-fogalmak)
3. [Amire szükséged lesz](#3-amire-szükséged-lesz)
4. [Telepítés a szerverre](#4-telepítés-a-szerverre)
5. [Első beállítás: StremHU, AIOStreams, TMDB, Stremio, LeLibrary, feliratok](#5-első-beállítás)
6. [Címek és jelszavak – mi hol van?](#6-címek-és-jelszavak)
7. [A `.env` fájl](#7-a-env-fájl)
8. [TorBox-helykezelés](#8-torbox-helykezelés)
9. [Szerverpult, állapotfigyelő, mentés](#9-szerverpult-a-szerveren)
10. [Helyi Szerverpult (Windows)](#10-helyi-szerverpult-windows)
11. [Frissítés](#11-frissítés)
12. [Biztonság és adatvédelem](#12-biztonság-és-adatvédelem)
13. [Hibaelhárítás](#13-hibaelhárítás)
14. [Hasznos parancsok](#14-hasznos-parancsok)
15. [Futtatás otthoni Windows-gépen](#15-futtatás-otthoni-windows-gépen)
16. [Fejlesztői információk](#16-fejlesztői-információk)

---

## 1. Áttekintés

```
Stremio ──keresés──► AIOStreams ──► StremHU (nCore-keresés, .torrent)
                          │
                          └──.torrent──► StremThru ──► TorBox (letölt, tárol, seedel)
                                                          │
Stremio ◄──────────── lejátszás közvetlenül a TorBoxról ──┘
```

- A **szerver** (pl. egy Contabo VPS) csak keres és továbbít – kis forgalmú API-hívások. A filmek **nem** töltődnek le rá, és nem rajta keresztül mennek a lejátszáshoz.
- A **TorBox** tölti le a torrentet, tárolja, seedeli, és onnan streamel a Stremio. Ami már fent van a TorBoxon (gyorsítótár), az azonnal indul.
- A szerveren ezek a Docker-konténerek futnak:

| Konténer | Feladat | Elérhető |
|---|---|---|
| `stremhu` + `ts-stremhu` | StremHU (nCore) + a saját Tailscale-kapcsolata | nyilvános HTTPS (Funnel) |
| `aiostreams` + `ts-aiostreams` | AIOStreams – ezt használja a Stremio | nyilvános HTTPS (Funnel) |
| `tmdb` + `ts-tmdb` | saját TMDB-addon (magyar címek) | nyilvános HTTPS (Funnel) |
| `stremthru` | `.torrent` feltöltése a TorBoxra | csak belül |
| `lelibrary` + `ts-lelibrary` + `lelibrary-db` + `lelibrary-redis` | LeLibrary: a TorBoxon lévő tartalom poszterekkel a Stremio kezdőlapján | nyilvános HTTPS (Funnel) |
| `feliratok` + `ts-felirat` | magyar feliratok a feliratok.eu-ról (Stremio-felirataddon) | nyilvános HTTPS (Funnel) |
| `gatus` | állapotfigyelő, hiba esetén értesítés a telefonra | csak a tailnetről, vagy SSH-alagúton |
| `backrest` + `mentes-dump` | titkosított napi mentés felhőtárhelyre, egységes adatbázis-másolatokkal | csak a tailnetről, vagy SSH-alagúton |
| `pult` + `terminal` + `ts-pult` | Szerverpult és webes terminál | csak a tailnetről |
| *cron a szerveren* (`deploy/scripts/`) | önjavító figyelő (3 percenként) és heti automatikus frissítés | – |

## 2. Fogalmak

| Fogalom | Jelentése |
|---|---|
| **Docker, konténer** | Minden program egy elszigetelt „dobozban” (konténerben) fut, a hozzávalóival együtt. Nem kell egyenként telepíteni semmit. |
| **Image** | A konténer „telepítőkészlete”. Frissítéskor az új image-et tölti le a szerver. |
| **Docker Compose** | Egy fájl (`compose.yaml`) írja le az összes konténert; a `docker compose up -d` mindet elindítja. |
| **Tailscale, tailnet** | Biztonságos magánhálózat a saját eszközeid között. A tailnet a te hálózatod neve, pl. `valami-nev.ts.net`. Minden konténer saját „gépként” jelenik meg benne, pl. `https://aiostreams.valami-nev.ts.net`. |
| **Funnel** | A Tailscale funkciója, amellyel egy tailnetes cím az internetről is elérhető (HTTPS-sel). Ezért éri el a Stremio bárhonnan az AIOStreamst. |
| **Auth key** | Egyszer használatos belépőkulcs, amellyel a konténerek felcsatlakoznak a tailnetedre. |
| **TorBox** | Fizetős felhőszolgáltatás: letölti a torrentet, tárolja és streameli. |
| **Hely (slot)** | A TorBox egyszerre csak ennyi aktív torrentet kezel (Pro: 10). A seedelők is helyet foglalnak. |
| **Seed, Hit'n'Run** | Az nCore szabálya szerint a letöltött torrentet egy ideig vissza kell osztani (seedelni). Aki nem teszi, „Hit'n'Run”-t kap. |
| **Torznab** | Szabványos keresőfelület; ezen keresztül kérdezi az AIOStreams a StremHU-t. |
| **AIOStreams** | Stremio-addon, amely több forrásból gyűjti a találatokat, és a TorBoxra küldi őket. |
| **StremThru** | Kis segédprogram, amely a `.torrent` fájlt feltölti a TorBoxra. |
| **SSH** | Távoli parancssoros belépés a szerverre (`ssh root@szerver-címe`). |
| **`.env`** | A szerveren lévő beállítófájl a titkokkal (jelszavak, kulcsok). Soha nem kerül fel a GitHubra. |

## 3. Amire szükséged lesz

| Mi | Megjegyzés |
|---|---|
| **Linux-szerver Dockerrel** | pl. Contabo VPS, Ubuntu 22.04/24.04. 2 GB RAM elég. |
| **Tailscale-fiók** | ingyenes; [login.tailscale.com](https://login.tailscale.com) |
| **TorBox-előfizetés** | privát torrenthez fizetős csomag kell; a hosszabb seedelés miatt a Pro ajánlott |
| **nCore-fiók** | felhasználónév és jelszó (a StremHU-ban adod meg) |
| **TMDB API-kulcs** | ingyenes, a magyar címekhez; themoviedb.org → Beállítások → API |

**Kockázat:** a TorBox a te nCore-passkey-eddel, a saját IP-címeiről tölt és seedel. Az nCore nem szerepel a TorBox által hivatalosan tesztelt trackerek között.

## 4. Telepítés a szerverre

### 4.1 Belépés a szerverre

Windowson nyiss egy PowerShellt, és lépj be (a szerver címét és a root jelszót a tárhelyszolgáltató adja, a Contabo e-mailben küldi):

```powershell
ssh root@SZERVER-CÍME
```

### 4.2 Előkészületek a Tailscale-ben

1. **Tailnet neve:** [Tailscale admin → DNS](https://login.tailscale.com/admin/dns) – ilyesmi: `valami-nev.ts.net`.
2. **Auth key:** [Settings → Keys](https://login.tailscale.com/admin/settings/keys) → *Generate auth key* → **Reusable** legyen bekapcsolva (több konténer használja) → másold ki (`tskey-auth-…`).
3. **HTTPS és Funnel:** a tailneten legyen engedélyezve a HTTPS-tanúsítvány és a Funnel (a DNS-oldalon, illetve az *Access controls*-ban). Ha korábban már használtál Funnelt, ez rendben van.

### 4.3 Docker telepítése (ha még nincs)

```sh
curl -fsSL https://get.docker.com | sh
```

### 4.4 A telepítő futtatása

```sh
curl -fsSL https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/install.sh -o install.sh
sh install.sh
```

A telepítő:

1. letölti ezt a repót a `~/stremhu-source` mappába,
2. létrehozza a `.env` fájlt, és legenerálja a titkos kulcsokat és jelszavakat,
3. rákérdez:
   - **Tailnet neve** – pl. `valami-nev.ts.net`
   - **Tailscale auth key** – a `tskey-auth-…` kulcs
   - **TorBox API-kulcs** – TorBox → Settings → API (Enter = kihagyás)
   - **TMDB API-kulcs** (Enter = kihagyás)
4. letölti és elindítja a konténereket,
5. a végén kiírja a címeket és a Szerverpult belépési adatait.

Ha egy Tailscale-konténer nem tudott belépni (pl. lejárt az auth key), a telepítő kiír egy `https://login.tailscale.com/…` linket – nyisd meg böngészőben, és engedélyezd.

A telepítő többször is futtatható: a meglévő beállításokat nem írja felül, csak a hiányzókat pótolja.

### 4.5 Ellenőrzés

```sh
cd ~/stremhu-source/deploy
docker compose ps
```

Minden konténernél `Up` állapotot kell látnod.

### 4.6 Teendők a Tailscale adminban telepítés után

- [Machines](https://login.tailscale.com/admin/machines): mindegyik új gépnél (`stremhu-tb`, `aiostreams`, `tmdb`, `pult`) **… → Disable key expiry**, különben kb. 180 nap múlva kijelentkeznek.
- [Keys](https://login.tailscale.com/admin/settings/keys): ha minden gép bent van, az auth key visszavonható (a gépek bent maradnak).

## 5. Első beállítás

A címekben a `<tailnet>` helyére a saját tailneted nevét írd.

### 5.1 StremHU

1. Nyisd meg: `https://stremhu-tb.<tailnet>`
2. Hozd létre az admin fiókot, és add meg az nCore-belépést.
3. A főoldalon, a **Támogatott kliensek** résznél van a **Torznab** kártya: másold ki a **Torznab URL**-t és az **API kulcs**ot.

Ellenőrzés böngészőben (a kulcsot cseréld a sajátodra) – ha XML jön vissza, a StremHU rendben van:

```
https://stremhu-tb.<tailnet>/api/torznab/api?t=caps&apikey=API_KULCS
```

### 5.2 AIOStreams

1. Nyisd meg: `https://aiostreams.<tailnet>/stremio/configure`, és lépj be (felhasználó és jelszó: lásd [6. fejezet](#6-címek-és-jelszavak)).
2. **Services:** kapcsold be a **TorBox**-ot, és add meg a TorBox API-kulcsot.
3. **Addons:** add hozzá a **Torznab** beépített addont:
   - *Name:* `StremHU`
   - *Torznab URL:* `https://stremhu-tb.<tailnet>/api/torznab`
   - *API Key:* a StremHU API-kulcsa
   - *Services:* TorBox
   - *Search Mode:* **Auto** (a StremHU csak IMDb-azonosítóval keres)
4. Nemzetközi tartalomhoz bekapcsolhatsz más addont is (pl. Torrentio vagy TorBox Search) a TorBox szolgáltatással.
5. A beépített **proxyt hagyd kikapcsolva** – különben a stream a szerveren menne át, és lassú lenne.
6. Mentés, majd **Install** → megnyílik a Stremio.

A menünevek AIOStreams-verziónként kicsit eltérhetnek.

### 5.3 Saját TMDB-addon (magyar címek)

A nyilvános TMDB-addon szervere gyakran túlterhelt, ezért saját példány fut: `https://tmdb.<tailnet>`.

1. Ha a telepítéskor kihagytad a TMDB-kulcsot: írd be a `.env`-ben a `TMDB_API_KEY=` sorba, majd `docker compose up -d`.
2. Nyisd meg: `https://tmdb.<tailnet>/configure`
   - nyelv: **Magyar (hu-HU)**
   - **IMDb Integration: bekapcsolva** – enélkül a StremHU nem talál magyar torrentet
   - válaszd ki a katalógusokat → **Install**

Opcionális: a `FANART_API_KEY` sorba fanart.tv-kulcsot írva szebb logókat kapsz.

### 5.4 Stremio

- Maradjon telepítve: **AIOStreams**, a **saját TMDB-addon**, a **LeLibrary** és a **Magyar feliratok** ([5.5](#55-lelibrary--a-torbox-könyvtár-a-kezdőlapon)). A Cinemeta beépített, nem törölhető – nem zavar.
- Töröld: a régi, nyilvános TMDB-addont és minden olyan régi addont, amely ugyanazt csinálja (különben duplán jelennek meg a találatok).
- **Profilok** (Stremio Supporter-csomag): minden profilnak **saját addonlistája** van. Az addonokat profilonként egyszer telepíteni kell – a Master profilban telepített addon a többi profilban nem jelenik meg.
- Az addonlista a fiókhoz (profilhoz) kötődik, és a Stremio induláskor frissíti. Ha egy eszközön még a régi látszik: zárd be teljesen az appot (Androidon *Kényszerített leállítás*), és nyisd meg újra; ha így sem, jelentkezz ki és be.
- Az **AIOStreams beállításai** (szűrés, rendezés, források) azonnal mindenhol érvényesek, újratelepítés nélkül. Újratelepíteni csak katalógus- vagy addonnév-változásnál kell.

**Próba:** keress rá egy filmre, ami biztosan fent van nCore-on. A `StremHU` találatok között lesznek azonnal indíthatók (TorBox-gyorsítótár) és letöltendők. Nem gyorsítótárazott találatnál az első indításkor meg kell várni, amíg a TorBox letölti (addig egy „próbáld később” videót kapsz).

### 5.5 LeLibrary – a TorBox-könyvtár a kezdőlapon

A [LeLibrary](https://github.com/leleasley/LeLibrary) a TorBoxon lévő filmeket és sorozatokat poszterekkel, sorokba rendezve mutatja a Stremio kezdőlapján. A sorozatokat évadokra és részekre bontja, és lejátszáskor elsőként a TorBoxon már fent lévő (azonnal induló) példányt kínálja. A csomag magyarított változatot futtat: a nyelv alapból magyar, és be van kapcsolva az IMDb-azonosítók használata – így az adatlapot a saját TMDB-addon adja, és **működik a következő rész automatikus indítása**.

1. Nyisd meg: `https://lelibrary.<tailnet>/configure`
2. Válaszd a **Settle In** szintet (később bármikor bővíthető újratelepítés nélkül).
3. **TorBox:** kapcsold be, és add meg a TorBox API-kulcsot.
4. **TMDB-kulcs:** ugyanaz, mint a TMDB-addoné (`grep TMDB_API_KEY ~/stremhu-source/deploy/.env`).
5. Ellenőrizd: **Language: Magyar**, **Use Main Metadata: bekapcsolva**.
6. Adj meg egy **beállítási jelszót** (legalább 8 karakter) – csak a módosításhoz kell.
7. **Create my setup** → a záró oldalon megkapod az **install linket** és a **szerkesztő linket**. Mentsd el őket a jelszóval együtt a pult Széfjébe. Az install link titkos: aki ismeri, a te könyvtáradból nézhet.
8. **Telepítés a Stremióba, minden profilba:** az *Open in Stremio* gomb abba a profilba telepít, amelyik épp aktív. A többi profilnál váltsd át a profilt, és nyisd meg újra az install linket (vagy illeszd be a Stremio *Addons* keresőjébe).

A sorozatoknál a következő rész magától indul, ha a Stremio lejátszó beállításaiban be van kapcsolva a következő rész automatikus lejátszása. Új letöltés a TorBoxon pár percen belül megjelenik a könyvtárban.

Beállítás módosítása: nyisd meg a szerkesztő linket, és add meg a jelszót – a változás újratelepítés nélkül érvényes, kivéve ha új sort (katalógust) adsz hozzá.

### 5.6 Magyar feliratok (feliratok.eu)

Saját felirataddon: a [feliratok.eu](https://feliratok.eu) (Super Subtitles) magyar feliratait adja a Stremióhoz – a sorozatokhoz részenként (évadcsomagból is kibontva), a filmekhez cím és év alapján. A régi feliratok ékezeteit (ő, ű) javítja, és a lejátszott fájl kiadásához (pl. `WEB-DL`, `1080p`, a kiadócsoport) legjobban illőt teszi előre.

1. Nyisd meg: `https://felirat.<tailnet>` → **Telepítés a Stremióba** (vagy a Stremio *Addons* keresőjébe: `https://felirat.<tailnet>/manifest.json`).
2. Ezt is **minden profilba** telepítsd.
3. A Stremio beállításaiban (*Player → Subtitles*) állítsd az alapértelmezett feliratnyelvet **Magyar**ra.

Lejátszás közben a feliratmenüben *Hungarian* néven jelennek meg (a beépített OpenSubtitles magyar feliratai mellett). Ha egy filmhez/részhez nincs, a feliratok.eu-n sincs fent.

Próba a szerveren (élő lekérdezés néhány ismert címre):

```sh
docker compose exec feliratok python -m app.selftest
docker compose exec feliratok python -m app.selftest tt0944947:1:1   # sorozat: IMDb-azonosító:évad:rész
```

## 6. Címek és jelszavak

| Mi | Cím | Belépés | Hol van a jelszó? |
|---|---|---|---|
| StremHU | `https://stremhu-tb.<tailnet>` | a saját admin fiókod | te adtad meg az első belépéskor |
| AIOStreams | `https://aiostreams.<tailnet>/stremio/configure` | `admin` | `.env` → `AIOSTREAMS_AUTH=admin:JELSZÓ` |
| TMDB-addon | `https://tmdb.<tailnet>/configure` | nincs | – |
| LeLibrary | `https://lelibrary.<tailnet>/configure` | a beállítás szerkesztő linkje + jelszó | te adtad meg a beállításkor |
| Magyar feliratok | `https://felirat.<tailnet>` | nincs | – |
| Állapotfigyelő | `https://pult.<tailnet>:8090` (tailnetről) vagy `http://localhost:8090` (SSH-alagúton) | nincs | – |
| Mentés (Backrest) | `https://pult.<tailnet>:9898` (tailnetről) vagy `http://localhost:9898` (SSH-alagúton) | a Backrestben létrehozott felhasználó | te adtad meg az első belépéskor |
| Szerverpult | `https://pult.<tailnet>` (csak tailnetről) | `admin` | `.env` → `PULT_PASSWORD` |
| Webes terminál | `https://pult.<tailnet>:8443` (csak tailnetről) | mint a Szerverpult | `.env` → `PULT_PASSWORD` |
| Helyi Szerverpult | a `Szerverpult.exe` nyitja meg | nincs (csak a saját gépedről) | – |
| Szerver (SSH) | `ssh root@SZERVER-CÍME` | `root` | a tárhelyszolgáltatótól |

Jelszó lekérdezése a szerveren:

```sh
grep -E 'AIOSTREAMS_AUTH|PULT_PASSWORD' ~/stremhu-source/deploy/.env
```

Tipp: mindezt elmentheted a pult **Széf** fülén (titkosítva).

## 7. A `.env` fájl

Helye: `~/stremhu-source/deploy/.env`. Szerkesztés: `nano ~/stremhu-source/deploy/.env` (mentés: `Ctrl+O`, Enter; kilépés: `Ctrl+X`). Módosítás után: `cd ~/stremhu-source/deploy && docker compose up -d`.

| Változó | Jelentés |
|---|---|
| `TAILNET` | a tailnet neve, pl. `valami-nev.ts.net` |
| `TS_AUTHKEY` | Tailscale auth key (csak az első belépéshez kell) |
| `STREMHU_HOSTNAME`, `AIOSTREAMS_HOSTNAME`, `TMDB_HOSTNAME`, `PULT_HOSTNAME` | a gépek neve a tailneten (ebből lesz a cím) |
| `TMDB_API_KEY`, `FANART_API_KEY` | a TMDB-addon kulcsai |
| `LELIBRARY_HOSTNAME`, `FELIRAT_HOSTNAME` | a LeLibrary és a felirataddon gépneve a tailneten |
| `NTFY_TOPIC` | az állapotfigyelő értesítéseinek témája az ntfy alkalmazásban (generált; titokként kezeld) |
| `LELIBRARY_CONFIG_SECRET`, `LELIBRARY_SELFHOST_SECRET`, `LELIBRARY_DB_PASSWORD` | a LeLibrary titkai (generált) – **első indítás után ne változtasd meg**, különben a mentett beállítások és az adatbázis elérhetetlenné válnak |
| `STREMHU_IMAGE` | melyik StremHU-image fusson (alapból ennek a forknak a buildje) |
| `STREMHU_SESSION_SECRET` | a StremHU belső titka (generált, ne add ki) |
| `TORBOX_API_KEY` | TorBox-kulcs a helykezeléshez; üresen a helykezelés nem fut |
| `TORBOX_CLEANUP_DRY_RUN` | `true` = próbaüzem: csak jelent, nem nyúl semmihez |
| `TORBOX_SLOT_LIMIT`, `TORBOX_MIN_FREE_SLOTS`, `TORBOX_MAX_PAUSE_HOURS`, `TORBOX_MIN_SEED_HOURS` | a helykezelés beállításai ([8. fejezet](#8-torbox-helykezelés)); ha nincsenek a fájlban, az alapérték érvényes |
| `AIOSTREAMS_SECRET_KEY` | az AIOStreams titkosító kulcsa – **első indítás után ne változtasd meg**, különben a mentett beállítások elvesznek |
| `AIOSTREAMS_AUTH` | AIOStreams-belépés `felhasználó:jelszó` formában |
| `PULT_USER`, `PULT_PASSWORD` | a Szerverpult és a webes terminál belépése |
| `REPO_DIR` | a repó helye a szerveren (a telepítő állítja be) |

## 8. TorBox-helykezelés

A TorBox csomagonként korlátozott számú aktív helyet ad (Pro: 10), és ebbe a seedelő torrentek is beleszámítanak. Ha betelt, az új letöltések sorba állnak – vagyis a film nem indul. A helykezelés ezt előzi meg. Ha a `TORBOX_API_KEY` meg van adva, a StremHU **5 percenként**:

1. **Szünetelteti** azokat a seedeket, amelyeknek már nincs kötelezettségük: az nCore Hit'n'Run-ja teljesült, vagy nyilvános torrentről van szó. **Nem töröl semmit** – a fájl a TorBoxon marad (újranézhető), és a TorBox a saját lejárati ideje után magától törli.
2. Ha **kevesebb mint 2 hely szabad**, a legfrissebb, még kötelezett nCore-seedet ideiglenesen szünetelteti (egyszerre legfeljebb annyit, amennyi a 2 szabad helyhez kell; a legfrissebbet, mert annak van a legtöbb ideje a határidőig).
3. Az ideiglenesen szüneteltetettet visszakapcsolja, amint van hely, de **legkésőbb 6 óra után** mindenképp – a Hit'n'Run elsőbbséget élvez. Visszakapcsolás után 6 óráig nem szünetelteti újra.

Biztonsági szabályok: ismeretlen privát torrenthez, folyamatban lévő letöltéshez, vagy ha az nCore Hit'n'Run-listája épp nem érhető el, **nem nyúl**. Egy friss torrentet az első 24 órában akkor is kötelezettnek tekint, ha még nem jelent meg az nCore listáján.

### Állapotjelentés

A pultban: **Műveletek → TorBox-helykezelés jelentése**. Parancssorból:

```sh
cd ~/stremhu-source/deploy
docker compose exec stremhu python -m app.modules.torbox_seed.status        # a lényeg
docker compose exec stremhu python -m app.modules.torbox_seed.status --all  # minden torrent
```

Példa és magyarázat:

```
TorBox – utolsó ellenőrzés: 2 perce
Szabad hely: 7/10 (cél: legalább 2, ideiglenes szünet max. 6 óra)

Állapot    Hit-n-Run    Tracker          Név                    Művelet / megjegyzés
seedel     folyamatban  ncore #1234567   Film.2024.1080p…
szünetel   teljesítve   ncore #7654321   Másik.Film…            Hit'n'Run teljesítve (3 ó 10 p)
```

| Oszlop | Jelentése |
|---|---|
| **Állapot** | `seedel`, `tölt`, `szünetel`, `seed vége`, `lejárt`, `kész`, `elakadt`, `ellenőriz` |
| **Hit-n-Run** | `folyamatban` = még seedelni kell · `teljesítve` = letudva · `nincs` = nyilvános torrent · `ismeretlen` = nem nCore-ról jött, ezért nem nyúl hozzá |
| **Tracker** | melyik oldalról és melyik torrent (nCore-azonosító) |
| **Művelet / megjegyzés** | mit csinált most, vagy miért szünetel: *Hit'n'Run teljesítve*, *nyilvános torrent*, *helyfelszabadítás* (ideiglenes), *pihentetés* (nemrég kapcsolta vissza) |

A ⚠️ sor azt jelzi, hogy egy szüneteltetett torrent a TorBox szerint még mindig helyet foglal. Ha ez tartósan így marad, a TorBox a szüneteltetéssel nem szabadítja fel a helyet – ilyenkor a helykezelés beállításait felül kell vizsgálni.

### Beállítások

A `.env`-be írva (utána `docker compose up -d`):

| Változó | Alapérték | Jelentés |
|---|---|---|
| `TORBOX_SLOT_LIMIT` | 10 | a TorBox-csomag aktív helyei |
| `TORBOX_MIN_FREE_SLOTS` | 2 | ennyi hely legyen mindig szabad |
| `TORBOX_MAX_PAUSE_HOURS` | 6 | egy kötelezett seed legfeljebb ennyi órát szünetelhet egyszerre |
| `TORBOX_MIN_SEED_HOURS` | 24 | ennyi ideig tekint egy friss torrentet kötelezettnek, akkor is, ha még nincs az nCore listáján |
| `TORBOX_CLEANUP_DRY_RUN` | false | `true` = próbaüzem, csak jelent |

## 9. Szerverpult (a szerveren)

Böngészős kezelőfelület, amely a szerveren fut: `https://pult.<tailnet>`.

- **Csak a tailnetedről érhető el** – azon az eszközön, amelyről megnyitod, fusson a Tailscale ugyanazzal a fiókkal (telefonra is van alkalmazás). Kívülről, Tailscale nélkül nem látszik.
- Belépés: `admin` és a `PULT_PASSWORD` a `.env`-ből (a böngésző megjegyzi).

| Fül | Mire jó |
|---|---|
| **Áttekintés** | a szolgáltatások címei; zöld = elérhető, piros = nem válaszol. A *Szerkesztés* gombbal saját sorokat is felvehetsz. |
| **Docker** | konténerek állapota, *Napló* (utolsó 300 sor), *Újraindítás* |
| **Műveletek** | *Frissítés* (git pull + új image-ek + újraindítás), *TorBox-jelentés*, *Lemezhely*, *A pult frissítése* |
| **Széf** | jelszavak, kulcsok, címek titkosítva ([lásd lent](#a-széf)) |
| **Terminál** | teljes root parancssor a szerveren: `https://pult.<tailnet>:8443`, ugyanazzal a belépéssel |

> A pult root jogot ad a szerverhez: **soha ne kapcsolj rá Funnelt**, és ne add ki a jelszavát.

### A széf

- A tartalmat **a böngésződ titkosítja** a mesterjelszóval (AES-256-GCM, a kulcs PBKDF2-SHA256-tal, 600 000 körrel készül). A szerver vagy a program csak titkosított adatot tárol.
- **A mesterjelszó nem állítható vissza.** Időnként töltsd le a *Titkosított mentést* (zárolt állapotban látszik a gomb).
- 10 perc tétlenség után magától zárol.
- A mentés a két pult között kölcsönösen visszaállítható (*Mentés visszaállítása…* az új széf létrehozásánál).

### Állapotfigyelő és mentés Tailscale nélkül (SSH-alagút)

Az állapotfigyelő és a mentés a tailnet mellett **SSH-alagúton** is megnyitható, bármelyik gépről, amelyről be tudsz lépni a szerverre – Tailscale nélkül. A két felület a szerveren csak a szerver saját gépén (`127.0.0.1`) figyel, kívülről nem érhető el; az SSH-kapcsolat „hozza át” a saját gépedre.

1. Windowson nyiss egy PowerShellt, és írd be (a `SZERVERED-CÍME` helyére a szervered címét):
   ```powershell
   ssh -N -L 8090:127.0.0.1:18090 -L 9898:127.0.0.1:19898 root@SZERVERED-CÍME
   ```
2. Add meg a szerver jelszavát. Utána **látszólag nem történik semmi** – ez így jó: amíg ez az ablak nyitva van, él az alagút.
3. A böngészőben: **Állapotfigyelő:** `http://localhost:8090`, **Mentés:** `http://localhost:9898`.
4. Ha végeztél, a PowerShell-ablakban `Ctrl+C`, vagy csak zárd be.

### Állapotfigyelő és értesítések (Gatus)

`https://pult.<tailnet>:8090` (vagy SSH-alagúton `http://localhost:8090`) – egy oldalon látszik, mi működik: StremHU, AIOStreams, TMDB-addon, LeLibrary, a felirataddon, a belső StremThru, valamint a TorBox, az nCore és a feliratok.eu. A saját szolgáltatásokat 2 percenként, a külsőket 5–10 percenként ellenőrzi, kívülről – úgy, ahogy a családtagok Stremiója is látja.

**Értesítés a telefonra:** ha valami 3 egymás utáni ellenőrzésen elbukik, értesítést kapsz, és akkor is, amikor helyreállt.

1. Telepítsd az **ntfy** alkalmazást (Google Play / App Store).
2. **+** (Subscribe to topic) → írd be a témát: a telepítő a végén kiírja, vagy `grep NTFY_TOPIC ~/stremhu-source/deploy/.env`.
3. Kész. A téma neve titok: aki ismeri, látja az értesítéseket – ne add ki.

A figyelt címek listája: `deploy/gatus/config.yaml`.

### Mentés (Backrest)

A `mentes-dump` konténer 6 óránként egységes másolatot készít a StremHU, az AIOStreams és a LeLibrary adatbázisáról. A **Backrest** ezeket a pult széfjével, a `.env`-vel és a Tailscale-állapotokkal együtt naponta, **titkosítva** egy felhőtárhelyre menti. Ha a szerver tönkremegy, egy új szerveren minden visszaállítható – újratelepítés és újrabeállítás nélkül.

**Egyszeri beállítás** (kb. 25 perc). Tárhely: a saját **Google Drive**-od. A mentés néhány tíz MB, és csak titkosítva kerül fel: a Drive-on értelmetlen fájlok látszanak egy `stremhu-mentes` mappában.

**1. Saját Google-kulcs** (Google Cloud, a böngészőben). Az rclone közös Google-kulcsát 2026 során leállítják, ezért saját kulcs kell. **Még az első mentés előtt csináld meg:** `drive.file` jogosultsággal minden kulcs csak a saját maga által létrehozott fájlokat látja, így egy másik kulccsal készült mentéshez később nem férne hozzá.

Minden oldalon ellenőrizd, hogy felül a `stremhu-mentes` projekt van kiválasztva.

1. [console.cloud.google.com](https://console.cloud.google.com/) – bejelentkezés **azzal a Google-fiókkal, amelyiknek a Drive-jára mentesz**.
2. [Új projekt](https://console.cloud.google.com/projectcreate): *Project name:* `stremhu-mentes` → **Create**, majd az értesítésben **Select project**.
3. [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com) → **Enable**.
4. [Google Auth Platform](https://console.cloud.google.com/auth/overview) → **Get started**: *App name* `StremHU mentés`, *User support email* a saját címed → *Audience:* **External** → *Contact information:* a saját címed → elfogadás → **Create**.
5. [Data access](https://console.cloud.google.com/auth/scopes) → **Add or remove scopes** → szűrő: `drive.file` → pipáld be a `.../auth/drive.file` sort → **Update** → **Save**.
6. [Audience](https://console.cloud.google.com/auth/audience) → **Publish app** → **Confirm**. (Tesztüzemben a Google hetente érvényteleníti a belépést, és a mentés leállna.)
7. [Clients](https://console.cloud.google.com/auth/clients) → **Create client** → *Application type:* **Desktop app**, *Name:* `rclone` → **Create**. A **Client ID**-t és a **Client secret**-et másold ki és tedd a Széfbe – a secretet a Google csak most mutatja meg.

**2. Google Drive-kapcsolat (rclone)** – a saját gépeden készül, mert ott van böngésző, utána átkerül a szerverre.

1. Windowson, PowerShellben: `winget install Rclone.Rclone`, utána nyiss egy **új** PowerShell-ablakot.
2. Futtasd (bekéri a Client ID-t és a secretet, majd megnyitja a böngészőt):
   ```powershell
   $id = Read-Host "Client ID"; $sec = Read-Host "Client secret"; rclone config create gdrive drive scope=drive.file "client_id=$id" "client_secret=$sec"
   ```
3. A böngészőben válaszd ki a fiókot. A *„A Google nem ellenőrizte ezt az alkalmazást”* figyelmeztetésnél: **Speciális** → **Ugrás: StremHU mentés (nem biztonságos)** – ez a saját alkalmazásod. Ha jelölőnégyzet van, pipáld be → **Folytatás**.
4. Küldd át a kapcsolatot a szerverre, majd töröld a gépedről (a belépési kulcs van benne):
   ```powershell
   scp "$env:APPDATA\rclone\rclone.conf" root@SZERVER-CÍME:/tmp/rclone.conf
   Remove-Item "$env:APPDATA\rclone\rclone.conf"
   ```
5. A szerveren:
   ```bash
   cd ~/stremhu-source/deploy
   docker compose cp /tmp/rclone.conf backrest:/root/.config/rclone/rclone.conf && rm /tmp/rclone.conf
   docker compose exec backrest rclone about gdrive:
   ```
   Az utolsó parancsnak a Drive méretét és szabad helyét kell kiírnia, a *„shared Google Drive client_id”* figyelmeztetés nélkül.

**3. Backrest** – nyisd meg: `https://pult.<tailnet>:9898` vagy SSH-alagúton `http://localhost:9898` (lásd fent), első alkalommal hozz létre egy felhasználót és jelszót.

1. **Add Repo:**
   - *Repo Name:* `gdrive`
   - *Repository URI:* `rclone:gdrive:stremhu-mentes`
   - *Password:* hosszú, véletlen jelszó (a *Generate* gombbal) – **mentsd el a Széfbe: enélkül a mentés nem állítható vissza!**
   - a többi maradhat üresen
2. **Add Plan:**
   - *Plan Name:* `napi`, *Repository:* `gdrive`
   - *Paths:* `/userdata`
   - *Schedule:* naponta egyszer (pl. hajnali 4-kor)
   - *Retention:* pl. 7 napi, 4 heti, 6 havi mentés
3. **Backup Now** – fél-egy perc, utána a *Snapshots* alatt látszik, a Drive-on pedig megjelenik a `stremhu-mentes` mappa.

Ha a Google-engedély egyszer lejárna vagy visszavonnád (a mentés hibát jelez), a 2. lépést kell megismételni – ugyanazzal a Client ID-val és secrettel, különben az új kapcsolat nem látja a korábbi mentéseket.

Mi kerül a mentésbe (`/userdata`): `adatbazisok` (StremHU, AIOStreams, LeLibrary), `szerverpult` (a pult széfje és szolgáltatáslistája), `lelibrary`, `telepito` (a `.env` és a telepítőcsomag), `tailscale` (a gépek belépési állapota). Visszaállításkor (új szerveren): telepítés után a 2. lépéssel – ugyanazzal a Client ID-val és secrettel – újra fel kell venni a `gdrive` kapcsolatot, a Backrestben ugyanezt a repót ugyanazzal a jelszóval, majd a *Snapshots* alatt visszaállítani.

## 10. Helyi Szerverpult (Windows)

Ugyanez a pult Windows-programként, a saját gépeden. **Nem kell hozzá Tailscale**: a szervert SSH-n éri el, ugyanúgy, mint a PowerShell. Pluszban a Contabo VNC-t egy gombbal megnyitja az UltraVNC-ben.

### Telepítés és első indítás

1. Töltsd le: [Szerverpult.exe](https://github.com/hubbert0/stremhu-source/releases/download/helyi-pult/Szerverpult.exe), és tedd egy állandó helyre (pl. Asztal).
2. Dupla kattintás. A Windows első alkalommal figyelmeztethet (nem aláírt program): *További információ → Futtatás mindenképp*.
3. Megnyílik egy **fekete ablak** – ez maga a program, hagyd nyitva (bezárásával leáll) – és a **böngésző**.
4. **Beállítások** fül – pontosan így töltsd ki:

| Mező | Mit írj be | Példa |
|---|---|---|
| Szerver címe | ugyanaz, mint az `ssh root@…` parancsban – **`https://` és port nélkül** | `szerver.example.com` |
| Felhasználó | `root` | `root` |
| Port | az SSH portja, szinte mindig `22` (nem 443!) | `22` |
| A repó mappája | maradhat | `/root/stremhu-source` |
| A szerver jelszava | a root jelszó, amit az `ssh` belépéskor kér (nem a Windows-jelszó) | |

   → **Kapcsolódás és kulcs telepítése**. A program saját SSH-kulcsot tesz a szerverre, a jelszót **nem menti el**, és a szerver `.env`-jéből magától kiolvassa a tailnet nevét és a címeket. Siker esetén jobb felül: *SSH: kapcsolódva*.

   Ha a szerveren ki van kapcsolva a jelszavas belépés: nyisd le a *Jelszó nélkül, kézzel telepített kulccsal* részt, futtasd az ott látható parancsot a szokott SSH-belépés után, majd jelszó nélkül kattints a gombra.

### Fülek

Mint a szerveres pultnál, a különbségek:

- **Terminál:** a böngészőben, a lapon belül nyílik. Másolás: kijelölés + `Ctrl+Shift+C`, beillesztés: `Ctrl+Shift+V` vagy jobb klikk.
- **Műveletek:** *Frissítés* (mindent frissít, a szerveres pultot is), *Konténerek állapota*, *TorBox-jelentés*, *Lemezhely*, *Felesleges régi image-ek törlése* (helyet szabadít fel a szerveren; csak a már nem használt image-eket törli).
- **Széf → UltraVNC:** a bejegyzésnél pipáld be a *VNC-kapcsolat* négyzetet; a cím `IP:port` legyen (pl. `203.0.113.10:63100`), a jelszó a VNC-jelszó. A kártyán megjelenik a **Megnyitás UltraVNC-vel** gomb. A program magától megkeresi a `vncviewer.exe`-t; ha máshová telepítetted, a Beállításokban megadhatod a helyét.

### Jó tudni

- Ha bezártad a böngészőlapot, indítsd el újra az exe-t – ha már fut, csak újra megnyitja a lapot.
- Csak a saját gépedről érhető el (`127.0.0.1`, 8765-ös port); más gép és más weboldal nem fér hozzá.
- Adatai a `%APPDATA%\Szerverpult` mappában vannak: beállítások, SSH-kulcs (`id_ed25519`), titkosított széf. Új gépre költözéskor ezt a mappát vidd át.
- A program az első kapcsolódáskor megjegyzi a szerver „ujjlenyomatát”. Ha a szervert újratelepíted, a Beállításokban kapcsolódj újra.
- Hozzáférés visszavonása: a szerveren a `/root/.ssh/authorized_keys` fájlból töröld a `szerverpult` végű sort.
- **Mikor kell a VNC?** Ha a szerver nem indul, vagy a hálózaton nem érhető el (SSH sem megy). Ilyenkor csak a tárhelyszolgáltató VNC-konzolja segít.

## 11. Frissítés

A `torbox` ágra kerülő minden módosítás után a GitHub automatikusan új image-eket készít. Frissítés:

- **Helyi Szerverpult** vagy **Szerverpult** → **Műveletek → Frissítés**, vagy
- SSH-n:

  ```sh
  cd ~/stremhu-source && git pull && cd deploy && sh install.sh
  ```

A frissítés letölti a repó és az image-ek új változatát, és újraindítja a megváltozott konténereket (1–2 perc kiesés lehet). A beállításaid, fiókjaid, a széf és a `.env` megmaradnak.

### Automatikus karbantartás

A telepítő két ütemezett feladatot állít be a szerveren (cron). Mindkettő az állapotfigyelő ntfy-témájára küld értesítést, így a telefonodon látod, mi történt.

**Önjavító figyelő – 3 percenként.** Kívülről (a Funnelen át, ahogy a családtagok Stremiója is látja) megnézi az öt nyilvános címet: StremHU, AIOStreams, TMDB-addon, LeLibrary, Magyar feliratok. Ha egy cím **3 egymás utáni ellenőrzésen** (kb. 10 percig) nem válaszol, a hozzá tartozó két konténert (Tailscale + program) újraindítja, és értesítést küld: *„LeLibrary újraindítva”*.
- Ugyanazt a szolgáltatást **legfeljebb óránként egyszer** indítja újra; ha az sem segít, az állapotfigyelő továbbra is jelez, és kézzel kell ránézni.
- Ha **mind az öt** cím egyszerre elérhetetlen, nem indít újra semmit: az hálózati vagy Tailscale-hiba, amin az újraindítás nem segít.
- A heti frissítés közben nem fut.

**Heti frissítés – vasárnap hajnali 5-kor** (a szerver órája szerint, a 4 órás mentés után). Letölti a repó és az image-ek új változatát, felügyelet nélküli módban lefuttatja a telepítőt (kérdések nélkül, a `.env` új sorait pótolja), újraindítja, ami változott, és törli a régi image-eket. 3 perc múlva kívülről ellenőrzi a nyilvános címeket, és értesítést küld:
- *„Heti frissítés kész”* – halk értesítés, ha minden működik;
- *„Heti frissítés kész, de valami nem válaszol”* – ezt az önjavító kb. 10 percen belül kezeli;
- *„Heti frissítés: hiba”* – az utolsó naplósorokkal; ilyenkor semmi nem romlik el, csak a frissítés maradt el.

| | |
|---|---|
| Napló | `tail -n 30 /var/log/stremhu-karbantartas.log` |
| Kikapcsolás | a `.env`-ben `ONJAVITO=0` (figyelő) vagy `AUTO_FRISSITES=0` (heti frissítés) – a következő futástól érvényes |
| Kézi heti frissítés azonnal | `sh ~/stremhu-source/deploy/scripts/frissites.sh` |
| Az ütemezés | `/etc/cron.d/stremhu-torbox` (a telepítő minden futáskor újraírja) |

A Windowsos, otthoni futtatásnál ([15. fejezet](#15-futtatás-otthoni-windows-gépen)) nincs cron, ott ez a két feladat nem fut.

### Költözés új szerverre

Másik (pl. kisebb, olcsóbb) szerverre a `deploy/koltozes.sh` viszi át a rendszert. Minden átkerül: beállítások, adatbázisok, a széf, a mentés beállítása és a **Tailscale-azonosítók** – ezért minden cím ugyanaz marad, a család Stremiójában semmit nem kell átállítani. A kiesés kb. 5 perc.

1. Rendeld meg az új szervert (Ubuntu 24.04, ugyanabban a régióban). A root-jelszót tedd a Széfbe.
2. Lépj be az **új** szerverre: `ssh root@ÚJ-IP-CÍM` (az első belépéskor `yes`).
3. Futtasd ott:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/koltozes.sh -o koltozes.sh
   bash koltozes.sh RÉGI-IP-CÍM
   ```
   Kéri a **régi** szerver root-jelszavát, majd megkérdezi, mehet-e (`i`).
4. A szkript:
   - előkészíti az újat (Docker, repó, a régi `.env`-je, image-ek) – a régi közben még fut;
   - leállítja a régit (a konténereket nem törli), és ott kikapcsolja az automatikus karbantartást;
   - átmásolja az összes adatkötetet, és fájlszám + méret alapján ellenőrzi, hogy hiánytalan-e;
   - elindítja az újat, kívülről ellenőrzi az öt nyilvános címet (ami nem válaszol, azt egyszer újraindítja);
   - átveszi a régi szerver SSH-gépkulcsát és belépési kulcsait, így a saját gépeden a régi névvel (PowerShell, Szerverpult.exe) semmit nem kell újra beállítani.
5. A végén kiírja a teendőket: a szerver nevét (pl. myaddr.io) állítsd át az új IP-re, nézz bele a Stremióba, és pár nap múlva mondd le a régi szervert.

Ha a leállítás és az indítás között bármi hiba történik, a szkript **magától visszaindítja a régi rendszert**. A régi szervert a költözés után ne indítsd el újra: két azonos Tailscale-gép ütközne.

A Windowsos pult új verziója: töltsd le újra a [Szerverpult.exe](https://github.com/hubbert0/stremhu-source/releases/download/helyi-pult/Szerverpult.exe)-t, és cseréld le a régit (előtte zárd be a fekete ablakot). Az adataid megmaradnak.

**Ha a hivatalos StremHU átveszi a #283-as kiegészítést,** a `.env`-ben a `STREMHU_IMAGE` sor átírható `s4pp1/stremhu-source:latest`-re. Ilyenkor viszont a TorBox-helykezelés nem fut (az csak ebben a forkban van).

## 12. Biztonság és adatvédelem

- **A repó nyilvános** (nyilvános projekt forkja nem lehet privát), de nincs benne személyes adat: se szervercím, se tailnet-név, se jelszó vagy kulcs.
- **A titkok helye:** a szerveren a `.env` (csak root olvashatja), a Windows-gépen a `%APPDATA%\Szerverpult`, a széf tartalma titkosítva.
- **Nyilvános (Funnel) címek:** StremHU, AIOStreams, TMDB-addon, LeLibrary, felirataddon – mindegyik saját belépéssel, jelszóval vagy titkos linkkel védett. A StremThru, a LeLibrary adatbázisa és gyorsítótára kívülről nem látszik.
- **Csak tailnetről:** a Szerverpult és a webes terminál. Az állapotfigyelő és a mentés emellett SSH-alagúton is elérhető (a szerveren csak `127.0.0.1`-en figyelnek, kívülről nem látszanak; a mentéshez a Backrest saját jelszava is kell).
- **Mentés:** a Backrest restic-kel, a tárhelyre kerülés előtt titkosít; a repó jelszava nélkül a mentés sem olvasható, sem visszaállítható.
- **Ha egy kulcs vagy jelszó illetéktelen kezébe kerülhetett** (pl. elküldted valakinek): Tailscale auth key → visszavonás a Keys oldalon; TorBox API-kulcs → újragenerálás a TorBoxban, és csere a `.env`-ben és az AIOStreamsben; pult-jelszó → új érték a `.env`-ben, majd `docker compose up -d pult terminal`.

## 13. Hibaelhárítás

| Tünet | Mit nézz / mit csinálj |
|---|---|
| Egy cím nem töltődik be | `docker compose ps` – fut-e a konténer? Ha nem: `docker compose up -d`. Napló: `docker compose logs --tail 100 NÉV` |
| Egy nyilvános cím nem válaszol (a böngésző sokáig tölt, a pult *ConnectTimeout*-ot ír), a többi működik, a konténer pedig *healthy* | a Tailscale újraindításkor ritkán rossz állapotban marad. Az önjavító figyelő kb. 10 perc után magától újraindítja ([11. fejezet](#11-frissítés)); azonnal kézzel is lehet – a hozzá tartozó két konténer újraindítása megoldja, pl. a LeLibrarynél: `docker compose up -d --force-recreate ts-lelibrary lelibrary` (a többinél: `ts-stremhu stremhu`, `ts-aiostreams aiostreams`, `ts-tmdb tmdb`, `ts-felirat feliratok`), majd 1 perc várakozás |
| A Tailscale-konténer nincs bent | `docker compose exec ts-stremhu tailscale status` – ha *Logged out*, futtasd újra a telepítőt, és nyisd meg a kiírt belépő linket |
| `denied` / `unauthorized` image-letöltéskor | GitHubon: profil → *Packages* → a csomag → *Package settings* → *Change visibility* → **Public** |
| Nincs magyar találat a Stremióban | 1) a [5.1](#51-stremhu) ellenőrző címe XML-t ad-e; 2) a TMDB-addonban be van-e kapcsolva az IMDb Integration; 3) az AIOStreamsben jó-e a Torznab URL és a kulcs |
| A film nem indul, „próbáld később” | nem gyorsítótárazott torrent: a TorBox még tölti. Ha sokáig áll: TorBox-jelentés – van-e szabad hely? |
| A StremThru újraindul / memóriahiány | a `compose.yaml`-ben a `STREMTHRU_FEATURE` sor kikapcsolja a nagy szinkronokat; ne töröld |
| A LeLibrary-sorok üresek | a TorBox-kulcs és a TMDB-kulcs helyes-e (szerkesztő link); új letöltés pár perc után jelenik meg. Napló: `docker compose logs --tail 100 lelibrary` |
| Nincs magyar felirat | `docker compose exec feliratok python -m app.selftest IMDB-AZONOSÍTÓ` – ha itt sincs, a feliratok.eu-n sincs fent; napló: `docker compose logs --tail 100 feliratok` |
| Nem jön értesítés | az ntfy appban pontosan az `NTFY_TOPIC` témára iratkoztál-e fel; napló: `docker compose logs --tail 100 gatus` |
| A mentés hibát jelez | a Backrest *Operations* lapján látszik az ok (pl. lejárt Google-engedély: a 9. fejezet Mentés részének 2. lépése, ugyanazzal a Client ID-val); az adatbázis-másolatok: `docker compose logs --tail 20 mentes-dump` |
| A LeLibrary nem indul | `docker compose ps` – a `lelibrary-db` és a `lelibrary-redis` állapota `healthy` legyen; memória: `free -h` |
| A Szerverpult nem nyílik meg | azon az eszközön fut-e a Tailscale? Szerveroldalon: `docker compose exec ts-pult tailscale serve status` |
| Helyi pult: *Hibás adat: szerver címe* | a címet `https://` és port nélkül írd be ([10. fejezet](#telepítés-és-első-indítás)) |
| Helyi pult: *a szerver elutasította a belépést* | rossz felhasználó (legyen `root`) vagy jelszó; vagy a kulcs nincs telepítve |
| Helyi pult: *a szerver gépkulcsa megváltozott* | ha újratelepítetted a szervert: Beállítások → kapcsolódj újra; ha nem, gyanús – ne folytasd |
| Helyi pult: *Nem találom az UltraVNC-nézőt* | telepítsd az UltraVNC-t, vagy add meg a `vncviewer.exe` helyét a Beállításokban |
| A Windows Defender letiltja az exe-t | az ilyen módon csomagolt, aláíratlan programoknál ez előfordul (téves riasztás) |

## 14. Hasznos parancsok

Mindegyik a `~/stremhu-source/deploy` mappában (`cd ~/stremhu-source/deploy`):

| Parancs | Mit csinál |
|---|---|
| `docker compose ps` | konténerek állapota |
| `docker compose logs --tail 100 stremhu` | a StremHU utolsó 100 naplósora (a név cserélhető) |
| `docker compose restart aiostreams` | egy konténer újraindítása |
| `docker compose up -d` | mindent elindít / a `.env` módosítását érvényesíti |
| `docker compose pull && docker compose up -d` | új image-ek letöltése és indítása |
| `df -h /` | szabad lemezhely |
| `docker image prune -f` | régi, nem használt image-ek törlése |
| `tail -n 30 /var/log/stremhu-karbantartas.log` | az önjavító figyelő és a heti frissítés naplója |

## 15. Futtatás otthoni Windows-gépen

A rendszer Docker Desktoppal Windowson is fut (a 4. fejezet helyett):

1. **Letöltés:** GitHubon a `torbox` ágon *Code → Download ZIP*, majd csomagold ki a `C:\stremhu` mappába. (Ha van Git: `git clone -b torbox https://github.com/hubbert0/stremhu-source.git C:\stremhu\stremhu-source`)
2. PowerShellben generáld a `.env`-et:

   ```powershell
   cd C:\stremhu\stremhu-source\deploy
   powershell -ExecutionPolicy Bypass -File .\init-env.ps1
   notepad .env
   ```

   A Jegyzettömbben írd be a `TAILNET` és `TS_AUTHKEY` értékét, majd ments.
3. Indítás: `docker compose up -d`.

Hogy a gép állandó szerverként, újraindítás után is magától működjön:

- Docker Desktop → Settings → General → **Start Docker Desktop when you sign in**
- Windows **automatikus bejelentkezés** (a Docker Desktop csak bejelentkezés után indul el)
- **Alvó mód kikapcsolása**, laptopnál a fedél lecsukásakor is: „Ne csináljon semmit”

A szerveres pult (`pult`, `terminal`) Linux-szerverre készült; Windowson a Helyi Szerverpultot használd.

## 16. Fejlesztői információk

**Ágak:** `torbox` – a használt változat; `main` – a hivatalos StremHU másolata.

**Automatikus buildek** (`.github/workflows/`):

| Workflow | Mikor fut | Eredmény |
|---|---|---|
| `torbox-ghcr.yaml` | `server/`, `client/` változásakor | tesztek, majd `ghcr.io/hubbert0/stremhu-source:torbox` |
| `pult-ghcr.yaml` | `deploy/pult/` változásakor | tesztek, majd `ghcr.io/hubbert0/stremhu-pult:latest` |
| `feliratok-ghcr.yaml` | `deploy/feliratok/` változásakor | tesztek, majd `ghcr.io/hubbert0/stremhu-feliratok:latest` |
| `lelibrary-ghcr.yaml` | `deploy/lelibrary/` változásakor és hetente | a legfrissebb LeLibrary-kiadás a `deploy/lelibrary/patch.py` magyarításával: `ghcr.io/hubbert0/lelibrary-hu:latest` |
| `helyi-pult.yaml` | `deploy/helyi-pult/` változásakor | tesztek (Linuxon, SSH-teszt-szerverrel), Windows-build, indítási próba, majd a `helyi-pult` kiadásba feltöltés |

**A hivatalos StremHU frissítéseinek átvétele:**

```sh
git remote add upstream https://github.com/s4pp1/stremhu-source.git   # egyszer
git fetch upstream
git checkout torbox
git merge upstream/main
git push origin torbox
```

**Fontosabb fájlok:**

| Fájl | Tartalom |
|---|---|
| `deploy/compose.yaml` | az összes konténer leírása |
| `deploy/install.sh` | telepítő / frissítő |
| `deploy/tailscale/*.json` | a Tailscale Serve/Funnel beállításai (a `pult.json`-ban szándékosan nincs Funnel) |
| `server/app/modules/torbox_seed/` | TorBox-helykezelés: `logic.py` (döntések), `background_tasks.py` (5 perces futás), `report.py` (jelentés) |
| `deploy/pult/` | szerveres pult (FastAPI + ttyd) |
| `deploy/helyi-pult/` | Windowsos pult (FastAPI + asyncssh + xterm.js, PyInstaller) |
