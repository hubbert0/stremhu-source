# Nuvio-jelvények (Fusion-stílus)

Fekete címkék a Nuvio forráskártyáin, a Nuvio saját „MÉRET” jelzője mellett:
**FORRÁS: NCORE / TORRENTIO / COMET / MEDIAFUSION / STREMTHRU**, **HANG: MAGYAR / IDEGEN**,
**FELIRAT: MAGYAR**.

A Nuvio a forráskártya szövegében keres, ezért a jelvények az AIOStreams magyar
formátumához készültek (`📡 Forrás: …`, `🔊 Hang: 🇭🇺 magyar …`, `💬 Felirat: …` sorok).
A „HANG” jelvény csak akkor jelenik meg, ha a hang nyelve biztos (nCore-kategória
vagy bemért fájl); a csak fájlnévből kikövetkeztetett nyelvre nem.

Beállítás a Nuvióban: **Beállítások → Elrendezés → Fusion stílus → Fusion jelvény
URL-ek**, és ezt az URL-t kell hozzáadni:

```
https://raw.githubusercontent.com/hubbert0/stremhu-source/torbox/deploy/nuvio-jelvenyek/jelvenyek.json
```

Módosítás: a `keszit.py` elején lévő listát kell szerkeszteni, majd futtatni
(`python3 deploy/nuvio-jelvenyek/keszit.py`), és a képeket a JSON-nal együtt feltölteni.
