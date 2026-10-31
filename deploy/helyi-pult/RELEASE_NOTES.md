## Szerverpult – kezelőprogram Windowsra

A saját StremHU + TorBox szervered kezelése a böngészőből, a saját gépedről. **Tailscale nem kell hozzá**: a szervert SSH-n éri el, ugyanúgy, mint a PowerShell.

**Letöltés:** lent, az *Assets* részben a **Szerverpult.exe**. (A „Source code” fájlok a forráskódot tartalmazzák – a használathoz nem kellenek.)

### Mit tud

| Fül | Mire jó |
|---|---|
| **Áttekintés** | a szolgáltatások (StremHU, AIOStreams, TMDB-addon…) címei, zöld/piros elérhetőséggel |
| **Docker** | a szerver konténerei: állapot, napló, újraindítás |
| **Műveletek** | frissítés egy gombbal, TorBox-helykezelés jelentése, lemezhely, régi image-ek törlése |
| **Terminál** | parancssor a szerveren, közvetlenül a böngészőben |
| **Széf** | jelszavak, címek, kulcsok titkosítva; VNC-bejegyzés egy gombbal megnyílik az UltraVNC-ben |
| **Beállítások** | SSH-kapcsolat, UltraVNC helye |

### Első indítás

1. Töltsd le a **Szerverpult.exe**-t, és tedd egy állandó helyre (pl. Asztal).
2. Dupla kattintás. Ha a Windows figyelmeztet: *További információ → Futtatás mindenképp* (a program nincs aláírva).
3. Megnyílik egy fekete ablak – ez a program, hagyd nyitva – és a böngésző.
4. A **Beállítások** fülön:

| Mező | Mit írj be |
|---|---|
| Szerver címe | ami az `ssh root@…` parancsban a `@` után áll – **`https://` és port nélkül** |
| Felhasználó | `root` |
| Port | `22` |
| A repó mappája | maradhat: `/root/stremhu-source` |
| A szerver jelszava | a root jelszó (nem a Windows-jelszó) – a program nem menti el |

   → **Kapcsolódás és kulcs telepítése**. Siker esetén jobb felül: *SSH: kapcsolódva*.
5. A **Széf** fülön válassz mesterjelszót (nem állítható vissza!), és vedd fel az adataidat. VNC-hez: cím `IP:port`, jelszó a VNC-jelszó, és pipáld be a *VNC-kapcsolat* négyzetet.

Ha bezártad a böngészőlapot, indítsd el újra az exe-t – ha már fut, csak újra megnyitja a lapot.

### Adatok és biztonság

- Csak a saját gépedről érhető el (`127.0.0.1`); más gép vagy weboldal nem fér hozzá.
- A szerver jelszavát nem tárolja: saját SSH-kulcsot telepít, és megjegyzi a szerver ujjlenyomatát.
- Minden adat a `%APPDATA%\Szerverpult` mappában van (beállítások, SSH-kulcs, titkosított széf). Új gépre ezt a mappát vidd át.
- Frissítés: töltsd le újra az exe-t, és cseréld le a régit (előtte zárd be a fekete ablakot) – az adatok megmaradnak.

### Részletes leírás

[Dokumentáció – Helyi Szerverpult](https://github.com/hubbert0/stremhu-source/blob/torbox/deploy/README.md#10-helyi-szerverpult-windows) · [A teljes rendszer leírása](https://github.com/hubbert0/stremhu-source/blob/torbox/deploy/README.md)
