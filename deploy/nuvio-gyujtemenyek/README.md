# Nuvio-gyűjtemények

Csempés gyűjtemények a Nuvio kezdőlapjára. Az adatok a Nuvio beépített
TMDB-kapcsolatából jönnek, nem kell hozzá külön bővítmény vagy kulcs.

| Fájl | Tartalom |
|---|---|
| `streaming.json` | **Streamingszolgáltatók** |
| `ismeretterjeszto.json` | **Ismeretterjesztő** |
| `osszes.json` | a kettő együtt – a telefonos appba ezt érdemes importálni |

## Streamingszolgáltatók

Csempék: Netflix, HBO Max, Disney+, SkyShowtime, Prime Video, Apple TV+, Mesék.

- **Szolgáltatók** – négy sor: Filmek, Sorozatok, Mesefilmek, Mesesorozatok.
  Mind azt mutatja, ami **Magyarországon** épp elérhető az adott
  szolgáltatón, népszerűség szerint.
- **Mesék** – Mesefilmek és Mesesorozatok az összes fenti szolgáltatóról
  együtt, plusz Magyar mesefilmek és Magyar mesesorozatok (magyar gyártású
  animáció, szolgáltatótól függetlenül).

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
| Discovery | Discovery, Discovery Channel, Science, Discovery Science sorozatai |
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
származnak (külső tárhelyen), a SkyShowtime képe a Fusion Starter Kitből jön.
A többi saját: `kepek/keszit.py` rajzolja (`python3 kepek/keszit.py`).
