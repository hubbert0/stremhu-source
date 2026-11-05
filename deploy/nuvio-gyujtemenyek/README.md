# Nuvio-gyűjtemények

`streaming.json` – **Streamingszolgáltatók** gyűjtemény a Nuvio kezdőlapjára,
csempékkel: Netflix, HBO Max, Disney+, SkyShowtime, Prime Video, Apple TV+ és
Mesék. Az adatok a Nuvio beépített TMDB-kapcsolatából jönnek, nem kell hozzá
külön bővítmény vagy kulcs.

- **Szolgáltatók csempéi** – négy sor: Filmek, Sorozatok, Mesefilmek,
  Mesesorozatok. Mind azt mutatja, ami **Magyarországon** épp elérhető az adott
  szolgáltatón, népszerűség szerint.
- **Mesék csempe** – Mesefilmek és Mesesorozatok az összes fenti szolgáltatóról
  együtt, plusz Magyar mesefilmek és Magyar mesesorozatok (magyar gyártású
  animáció, szolgáltatótól függetlenül).

Mesének számít: filmeknél animáció + családi (horror, thriller, krimi, háborús
nélkül), sorozatoknál animáció + gyerek (krimi, hír, valóságshow, talkshow,
politika nélkül) – ugyanaz a szűrés, mint az Emby-könyvtár Mesék soraiban.

Importálás:

- **Telefonos / Windowsos Nuvio:** Beállítások → Gyűjtemények → Importálás,
  majd illeszd be a `streaming.json` tartalmát. (Ez az app az importtal
  lecseréli a meglévő gyűjteményeket.)
- **Nuvio TV:** Beállítások → Tartalom és felfedezés → Bővítmények →
  Kezelés telefonról → (a telefonon megnyíló oldalon) Gyűjtemények →
  Importálás → URL:

  ```
  https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/nuvio-gyujtemenyek/streaming.json
  ```

  majd Változtatások mentése.

A gyűjtemények fiókon belül, profilonként szinkronizálódnak a Nuvio-eszközök
között. Ha a címek angolul jelennek meg: Beállítások → Integrációk → TMDB
adatbővítés → Nyelv → magyar.

A szolgáltatók csempeképei egy közösségi Nuvio-csomagból származnak (külső
tárhelyen), a SkyShowtime képe a Fusion Starter Kitből jön, a Mesék képe
(`kepek/mesek.png`) saját.
