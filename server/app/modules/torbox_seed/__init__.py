"""TorBox seed-takarítás (hubbert0 fork).

A TorBox csomagonként korlátozott számú aktív helyet ad, és ebbe a seedelő
torrentek is beleszámítanak. Ez a modul rendszeresen törli a TorBoxról azokat
a privát tracker-torrenteket, amelyeknél a tracker Hit'n'Run-kötelezettsége
már teljesült, így a helyek nem a TorBox 30 napos seedelési ideje után,
hanem a szabály teljesítésekor szabadulnak fel.
"""
