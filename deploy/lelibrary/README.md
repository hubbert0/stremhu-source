# LeLibrary – magyarított build

A [LeLibrary](https://github.com/leleasley/LeLibrary) (szerző: leleasley, licenc: AGPL-3.0) a TorBoxon lévő
tartalmat poszterekkel mutatja a Stremio kezdőlapján. Ez a mappa csak a build előtti módosítást tartalmazza:

- `patch.py` – felveszi a magyart (`hu-HU`) a metaadat-nyelvek közé, és új beállításnál alapból a magyart
  és a *Use Main Metadata* (IMDb-azonosítók) módot választja.

A `.github/workflows/lelibrary-ghcr.yaml` a LeLibrary legfrissebb kiadását tölti le, alkalmazza a módosítást,
és `ghcr.io/hubbert0/lelibrary-hu` néven teszi közzé. A LeLibrary forráskódja az eredeti repóban érhető el;
a módosítás teljes egészében ebben a fájlban van.

Telepítés és beállítás: [deploy/README.md – 5.5](../README.md#55-lelibrary--a-torbox-könyvtár-a-kezdőlapon).
