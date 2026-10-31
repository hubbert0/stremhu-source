# StremHU + TorBox – dokumentáció

Ez a leírás végigvezet a rendszer telepítésén, beállításán és mindennapi kezelésén. Linux-tudás nem kell hozzá: minden parancs szó szerint bemásolható.

## Tartalom

1. [Áttekintés](#1-áttekintés)
2. [Fogalmak](#2-fogalmak)
3. [Amire szükséged lesz](#3-amire-szükséged-lesz)
4. [Telepítés a szerverre](#4-telepítés-a-szerverre)
5. [Első beállítás: StremHU, AIOStreams, TMDB, Stremio, LeLibrary](#5-első-beállítás)
6. [Címek és jelszavak – mi hol van?](#6-címek-és-jelszavak)
7. [A `.env` fájl](#7-a-env-fájl)
8. [TorBox-helykezelés](#8-torbox-helykezelés)
9. [Szerverpult (a szerveren, tailneten)](#9-szerverpult-a-szerveren)
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
| `pult` + `terminal` + `ts-pult` | Szerverpult és webes terminál | csak a tailnetről |

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

- Maradjon telepítve: **AIOStreams**, a **saját TMDB-addon** és a **LeLibrary** ([5.5](#55-lelibrary--a-torbox-könyvtár-a-kezdőlapon)). A Cinemeta beépített, nem törölhető – nem zavar.
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

## 6. Címek és jelszavak

| Mi | Cím | Belépés | Hol van a jelszó? |
|---|---|---|---|
| StremHU | `https://stremhu-tb.<tailnet>` | a saját admin fiókod | te adtad meg az első belépéskor |
| AIOStreams | `https://aiostreams.<tailnet>/stremio/configure` | `admin` | `.env` → `AIOSTREAMS_AUTH=admin:JELSZÓ` |
| TMDB-addon | `https://tmdb.<tailnet>/configure` | nincs | – |
| LeLibrary | `https://lelibrary.<tailnet>/configure` | a beállítás szerkesztő linkje + jelszó | te adtad meg a beállításkor |
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
| `LELIBRARY_HOSTNAME` | a LeLibrary gépneve a tailneten |
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

A Windowsos pult új verziója: töltsd le újra a [Szerverpult.exe](https://github.com/hubbert0/stremhu-source/releases/download/helyi-pult/Szerverpult.exe)-t, és cseréld le a régit (előtte zárd be a fekete ablakot). Az adataid megmaradnak.

**Ha a hivatalos StremHU átveszi a #283-as kiegészítést,** a `.env`-ben a `STREMHU_IMAGE` sor átírható `s4pp1/stremhu-source:latest`-re. Ilyenkor viszont a TorBox-helykezelés nem fut (az csak ebben a forkban van).

## 12. Biztonság és adatvédelem

- **A repó nyilvános** (nyilvános projekt forkja nem lehet privát), de nincs benne személyes adat: se szervercím, se tailnet-név, se jelszó vagy kulcs.
- **A titkok helye:** a szerveren a `.env` (csak root olvashatja), a Windows-gépen a `%APPDATA%\Szerverpult`, a széf tartalma titkosítva.
- **Nyilvános (Funnel) címek:** StremHU, AIOStreams, TMDB-addon, LeLibrary – mindegyik saját belépéssel, jelszóval vagy titkos linkkel védett. A StremThru, a LeLibrary adatbázisa és gyorsítótára kívülről nem látszik.
- **Csak tailnetről:** a Szerverpult és a webes terminál.
- **Ha egy kulcs vagy jelszó illetéktelen kezébe kerülhetett** (pl. elküldted valakinek): Tailscale auth key → visszavonás a Keys oldalon; TorBox API-kulcs → újragenerálás a TorBoxban, és csere a `.env`-ben és az AIOStreamsben; pult-jelszó → új érték a `.env`-ben, majd `docker compose up -d pult terminal`.

## 13. Hibaelhárítás

| Tünet | Mit nézz / mit csinálj |
|---|---|
| Egy cím nem töltődik be | `docker compose ps` – fut-e a konténer? Ha nem: `docker compose up -d`. Napló: `docker compose logs --tail 100 NÉV` |
| A Tailscale-konténer nincs bent | `docker compose exec ts-stremhu tailscale status` – ha *Logged out*, futtasd újra a telepítőt, és nyisd meg a kiírt belépő linket |
| `denied` / `unauthorized` image-letöltéskor | GitHubon: profil → *Packages* → a csomag → *Package settings* → *Change visibility* → **Public** |
| Nincs magyar találat a Stremióban | 1) a [5.1](#51-stremhu) ellenőrző címe XML-t ad-e; 2) a TMDB-addonban be van-e kapcsolva az IMDb Integration; 3) az AIOStreamsben jó-e a Torznab URL és a kulcs |
| A film nem indul, „próbáld később” | nem gyorsítótárazott torrent: a TorBox még tölti. Ha sokáig áll: TorBox-jelentés – van-e szabad hely? |
| A StremThru újraindul / memóriahiány | a `compose.yaml`-ben a `STREMTHRU_FEATURE` sor kikapcsolja a nagy szinkronokat; ne töröld |
| A LeLibrary-sorok üresek | a TorBox-kulcs és a TMDB-kulcs helyes-e (szerkesztő link); új letöltés pár perc után jelenik meg. Napló: `docker compose logs --tail 100 lelibrary` |
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
