"""TorBox-seedkezelés (hubbert0 fork).

A TorBox csomagonként korlátozott számú aktív helyet ad, és ebbe a seedelő
torrentek is beleszámítanak. Ez a modul rendszeresen leállítja azoknak a
torrenteknek a seedelését, amelyeket nem kell seedelni (nem nCore-ról jöttek,
vagy az nCore Hit'n'Run-kötelezettsége teljesült), a seedkötelezettekhez pedig
nem nyúl, és szól, ha valamelyik mégsem seedel. Nem töröl: a fájlok a TorBoxon
maradnak, és lejátszhatók.
"""
