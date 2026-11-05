# Nuvio-gyűjtemények

`streaming.json` – **Streamingszolgáltatók** gyűjtemény a Nuvio kezdőlapjára,
csempékkel: Netflix, HBO Max, Disney+, SkyShowtime, Prime Video, Apple TV+.
Minden csempe mögött két sor van (Filmek, Sorozatok): ami **Magyarországon**
épp elérhető az adott szolgáltatón, népszerűség szerint. Az adatok a Nuvio
beépített TMDB-kapcsolatából jönnek, nem kell hozzá külön bővítmény vagy kulcs.

Importálás:

- **Telefonos / Windowsos Nuvio:** Beállítások → Gyűjtemények → Importálás,
  majd illeszd be a `streaming.json` tartalmát. (Ez az app az importtal
  lecseréli a meglévő gyűjteményeket.)
- **Nuvio TV:** Beállítások → Tartalom és felfedezés → Bővítmények →
  Gyűjtemények → Importálás → URL:

  ```
  https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/nuvio-gyujtemenyek/streaming.json
  ```

A gyűjtemények fiókon belül, profilonként szinkronizálódnak a Nuvio-eszközök
között. Ha a címek angolul jelennek meg: Beállítások → Integrációk → TMDB
adatbővítés → Nyelv → magyar.

A csempeképek egy közösségi Nuvio-csomagból származnak (külső tárhelyen);
a SkyShowtime-nak nincs képe, ott a név látszik.
