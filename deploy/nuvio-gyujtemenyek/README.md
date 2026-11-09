# Nuvio-gyűjtemények

Csempés gyűjtemények a Nuvio kezdőlapjára. Az adatok a Nuvio beépített
TMDB-kapcsolatából jönnek, nem kell hozzá külön bővítmény vagy kulcs.

| Fájl | Tartalom |
|---|---|
| `streaming.json` | **Streamingszolgáltatók** |
| `mesek.json` | **Mesék** |
| `ismeretterjeszto.json` | **Ismeretterjesztő** |
| `ncore.json` | **nCore-ajánló** (kell hozzá a StremHU nCore-keresés (TorBox) bővítménye) |
| `osszes-tomor.json` | ugyanaz, mint az `osszes.json`, szóközök és üres mezők nélkül (telefonos beillesztéshez rövidebb) |
| `osszes.json` | mind a négy, ebben a sorrendben – a telefonos appba ezt érdemes importálni |

## Streamingszolgáltatók

Csempék: Netflix, HBO Max, Disney+, SkyShowtime, Prime Video, Apple TV+,
Színház és musical.

- **Szolgáltatók** – négy sor: Filmek, Sorozatok, Mesefilmek, Mesesorozatok –
  ami **Magyarországon** épp elérhető az adott szolgáltatón, népszerűség
  szerint.
- **Színház és musical** – a BroadwayHD kínálata (szolgáltató-azonosító 554,
  amerikai kínálat): népszerű, musicalek, színdarabok, családbarát, vígjátékok,
  romantikus, sorozatok; utána kulcsszavas sorok minden forrásból: felvett
  színházi előadások (filmed theater, stage show), Broadway, musicalfilmek,
  Broadway- és musicalsorozatok.

A sor nyugalmi kártyái szürkék, fehér logóval, kijelölve színesek (ezt a
`focusGifUrl` adja). A SkyShowtime szürke és színes képe is a Fusion Starter
Kitből jön, a Színház és musical kártyái sajátok.

## Mesék

| Csempe | Sorok |
|---|---|
| Mesefilmek | a hazai streamingeken; legújabbak; minden idők legjobbjai |
| Mesesorozatok | a hazai streamingeken; népszerű; legjobbra értékelt |
| Magyar mesék | magyar gyártású animációs filmek és sorozatok |
| Disney és Pixar | Walt Disney Pictures + Pixar + Walt Disney Animation Studios animációs filmjei; Disney Junior; Disney Channel |
| DreamWorks | a DreamWorks Animation filmjei és sorozatai |
| Studio Ghibli | a stúdió filmjei |
| Gyerekcsatornák | Disney Junior, Nick Jr., Nickelodeon, Cartoon Network, Disney Channel |

Mesének számít: filmeknél animáció + családi (horror, thriller, krimi, háborús
nélkül), sorozatoknál animáció + gyerek (krimi, hír, valóságshow, talkshow,
politika nélkül) – ugyanaz a szűrés, mint az Emby-könyvtár Mesék soraiban.

## Ismeretterjesztő

| Csempe | Sorok |
|---|---|
| Természet | természetfilmek, természetsorozatok (TMDB-kulcsszavak: nature, nature documentary, wildlife documentary, animals), Nat Geo Wild + Animal Planet + BBC Earth |
| Tudomány és űr | űr és csillagászat filmek/sorozatok (space, space exploration, astronomy, solar system), Science + Discovery Science |
| Történelem | történelmi dokumentumfilmek, History csatorna |
| Dokumentumfilmek | népszerű és legjobbra értékelt dokumentumfilmek és -sorozatok |
| National Geographic | National Geographic, National Geographic Channel, Nat Geo Wild sorozatai |
| Discovery | Discovery, Discovery Channel, Science, Discovery Science sorozatai (a kártya a Discovery+ logós képe) |
| CuriosityStream | a csatorna sorozatai + a szolgáltató filmjei (amerikai kínálat) |
| MagellanTV | a csatorna sorozatai + a szolgáltató filmjei (amerikai kínálat) |

A CuriosityStream és a MagellanTV filmjeit a TMDB csak az amerikai kínálatra
listázza megbízhatóan, ezért ott a régió `US`. Sok dokumentumfilmnek nincs
magyar címe vagy leírása a TMDB-ben – ezek angolul jelennek meg.

## Importálás

- **Telefonos / Windowsos Nuvio:** Beállítások → Gyűjtemények → Importálás,
  majd illeszd be az `osszes.json` tartalmát. Ez az app az importtal
  **lecseréli** a meglévő gyűjteményeket, ezért mindig a teljes fájlt
  illeszd be.
- **Nuvio TV:** Beállítások → Tartalom és felfedezés → Bővítmények →
  Kezelés telefonról → (a telefonon megnyíló oldalon) Gyűjtemények →
  Importálás → URL, majd Változtatások mentése. Ez hozzáadja/frissíti a
  gyűjteményeket, a többit megtartja:

  ```
  https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/nuvio-gyujtemenyek/osszes.json
  ```

A gyűjtemények fiókon belül, profilonként szinkronizálódnak a Nuvio-eszközök
között. Ha a címek angolul jelennek meg: Beállítások → Integrációk → TMDB
adatbővítés → Nyelv → magyar.

## Képek

A streamingszolgáltatók csempeképei egy közösségi Nuvio-csomagból
származnak (külső tárhelyen), a SkyShowtime és a Discovery képe a
[Fusion Starter Kitből](https://github.com/itsrenoria/fusion-starter-kit).

A többi kártya saját, a `kepek/keszit.py` rajzolja (`python3 kepek/keszit.py`):
színátmenet, [Noto Emoji](https://github.com/googlefonts/noto-emoji) 3D
rajzok (Apache 2.0) és [Nunito](https://fonts.google.com/specimen/Nunito)
betű (SIL OFL). A szkript a betűt és a rajzokat a `kepek/forras/` mappába
tölti le (nincs a repóban).

## nCore-ajánló

Négy kártya az nCore ajánló oldalából (a StremHU **nCore-keresés (TorBox)**
bővítménye adja, annak telepítve kell lennie):

| Csempe | Tartalom |
|---|---|
| Népszerű filmek | a jobb oldali „népszerű” lista és a „Legaktívabb” borítók filmjei |
| Népszerű sorozatok | ugyanez a sorozatoknál, a részek címenként összevonva |
| Staff ajánló – filmek | a „Staff által jelölt” filmek |
| Staff ajánló – sorozatok | a „Staff által jelölt” sorozatok |

A címek 3 óránként frissülnek. IMDb-linkes címnél a rendes adatlap nyílik (az
nCore-fül adja a forrásokat), különben nCore-csempe.

**Importálás:** a Nuvio importja az összes meglévő gyűjteményt lecseréli, ezért
az `osszes.json`-t érdemes betölteni (a meglévő hármat is tartalmazza).
