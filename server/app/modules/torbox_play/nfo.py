"""Hangsávok, feliratok és bitráta kiolvasása egy kiadás NFO-jából.

Az NFO-kat a kiadócsapatok kézzel írják, ezért több formát kezelünk:

- dísztáblás kulcs–érték sorok („Language.......: Hungarian”), akár két
  oszlopban („AUDIO 1 … AUDIO 2”);
- MediaInfo-kivonat („Audio #1” / „Text #2” szakaszok, „Language : Hungarian”);
- egysoros összefoglaló („Audio: Hungarian DD 5.1 / English DTS”,
  „Subtitles: Hungarian Forced & Full”).

Ha semmit nem találunk, üres eredményt adunk – ilyenkor a kiadás neve marad.
"""

import re
from dataclasses import dataclass, field

_ART = re.compile(r"[─-▟■-◿|¦]")
_SEGMENTS = re.compile(r"\s{3,}")
_KEY_VALUE = re.compile(
    r"^([A-Za-z][A-Za-z .()/#-]*?)\s*(?:\.{2,}\s*:?|:)\s*(.+?)\s*\.*$"
)
# Egy sorban hány „kulcs: érték” pár van (két oszlopos táblában kettő).
_MARKER = re.compile(r"(?:\.{2,}\s*:?|\s:)\s")
_AUDIO_KEYS = (
    "audio",
    "audio language",
    "audio languages",
    "language",
    "hang",
    "nyelv",
)
_SUBTITLE_KEYS = ("subtitles", "subtitle", "subs", "felirat", "feliratok")
_HEADER = re.compile(
    r"^(audio|text|subtitles?|video|general|menu|chapters?)"
    r"(?:\s*(?:#\s*)?\d+)?\s*(?:info(?:rmation)?s?)?\s*:?$",
    re.IGNORECASE,
)
_CHANNELS = re.compile(r"(?<![\d.])([1-9])\.([01])(?![\d.])")
_CHANNEL_COUNT = re.compile(r"(?<!\d)([1-8])\s*(?:ch\b|channels?\b|\()", re.I)
_BITRATE = re.compile(r"([\d][\d\s.,]*)\s*(k|m)(?:bps|b/s|bit/s)", re.IGNORECASE)

_LANGUAGES = {
    "hungarian": "magyar",
    "magyar": "magyar",
    "hun": "magyar",
    "hu": "magyar",
    "english": "angol",
    "angol": "angol",
    "eng": "angol",
    "en": "angol",
    "german": "német",
    "deutsch": "német",
    "ger": "német",
    "french": "francia",
    "fre": "francia",
    "spanish": "spanyol",
    "spa": "spanyol",
    "italian": "olasz",
    "ita": "olasz",
    "japanese": "japán",
    "jpn": "japán",
    "korean": "koreai",
    "russian": "orosz",
    "polish": "lengyel",
    "czech": "cseh",
    "slovak": "szlovák",
    "romanian": "román",
    "croatian": "horvát",
    "serbian": "szerb",
    "turkish": "török",
    "dutch": "holland",
    "swedish": "svéd",
    "danish": "dán",
    "norwegian": "norvég",
    "finnish": "finn",
    "portuguese": "portugál",
    "chinese": "kínai",
}
# Rövid kódokat csak önálló „Language” mezőben fogadunk el (különben az „en”
# vagy „hu” szövegrészletként is egyezne).
_SHORT_CODES = {"hun", "hu", "eng", "en", "ger", "fre", "spa", "ita", "jpn"}
_CHANNEL_NAMES = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}


@dataclass(frozen=True)
class AudioTrack:
    language: str
    channels: str = ""
    format: str = ""

    @property
    def label(self) -> str:
        return " ".join(p for p in (self.language, self.channels) if p)


@dataclass(frozen=True)
class SubtitleTrack:
    language: str
    kinds: tuple[str, ...] = ()  # „kényszerített”, „teljes”

    @property
    def label(self) -> str:
        if not self.kinds:
            return self.language
        return f"{self.language} ({' + '.join(self.kinds)})"


@dataclass
class NfoInfo:
    audio: list[AudioTrack] = field(default_factory=list)
    subtitles: list[SubtitleTrack] = field(default_factory=list)
    bitrate_kbps: int | None = None

    @property
    def empty(self) -> bool:
        return not self.audio and not self.subtitles and not self.bitrate_kbps

    @property
    def bitrate(self) -> str:
        if not self.bitrate_kbps:
            return ""
        if self.bitrate_kbps >= 1000:
            return f"{self.bitrate_kbps / 1000:.1f}".replace(".", ",") + " Mbps"
        return f"{self.bitrate_kbps} kbps"


def _language(value: str, *, allow_codes: bool) -> str | None:
    for word in re.findall(r"[A-Za-zÀ-ž]+", value.lower()):
        if word in _SHORT_CODES and not allow_codes:
            continue
        if word in _LANGUAGES:
            return _LANGUAGES[word]
    return None


def _languages(value: str) -> list[str]:
    found: list[str] = []
    for word in re.findall(r"[A-Za-zÀ-ž]+", value.lower()):
        language = _LANGUAGES.get(word) if word not in _SHORT_CODES else None
        if language and language not in found:
            found.append(language)
    return found


def _channels(value: str) -> str:
    match = _CHANNELS.search(value)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    count = _CHANNEL_COUNT.search(value)
    if count:
        return _CHANNEL_NAMES.get(int(count.group(1)), "")
    return ""


def _bitrate_kbps(value: str) -> int | None:
    match = _BITRATE.search(value)
    if not match:
        return None
    number = re.sub(r"\s", "", match.group(1)).replace(",", ".")
    # „15 900” vagy „15.900” ezres tagolás: ha a pont után pontosan 3 jegy van.
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", number):
        number = number.replace(".", "")
    try:
        amount = float(number)
    except ValueError:
        return None
    kbps = amount * 1000 if match.group(2).lower() == "m" else amount
    return int(kbps) if 32 <= kbps <= 200_000 else None


def _subtitle_kinds(value: str) -> tuple[str, ...]:
    lowered = value.lower()
    kinds: list[str] = []
    if "forced" in lowered or "kényszer" in lowered:
        kinds.append("kényszerített")
    if re.search(r"\b(full|teljes|complete)\b", lowered):
        kinds.append("teljes")
    return tuple(kinds)


@dataclass
class _Section:
    kind: str  # audio / text / video / general / other
    values: dict[str, str] = field(default_factory=dict)


def _section_kind(header: str) -> str:
    word = header.lower()
    if word.startswith("audio"):
        return "audio"
    if word.startswith(("text", "subtitle")):
        return "text"
    if word.startswith("video"):
        return "video"
    if word.startswith("general"):
        return "general"
    return "other"


def parse_nfo(text: str) -> NfoInfo:
    sections: list[_Section] = []
    columns: list[_Section] = []
    global_values: list[list[str]] = []
    # Többsoros érték (pl. „AUDIO....: Hungarian DD 5.1” alatt „English …”).
    continued: list[str] | None = None

    for raw in text.splitlines():
        line = _ART.sub(" ", raw).strip()
        # „Language            : Hungarian” (MediaInfo) ne essen két részre.
        line = re.sub(r":[ \t]+", ": ", re.sub(r"[ \t]+:", " :", line))
        if not line:
            continue
        segments = [s.strip() for s in _SEGMENTS.split(line) if s.strip()]
        headers = [s for s in segments if _HEADER.match(s)]
        if headers and len(headers) == len(segments):
            columns = [_Section(_section_kind(h)) for h in headers]
            sections.extend(columns)
            continued = None
            continue
        if len(_MARKER.findall(line)) <= 1:
            segments = [line]  # egy pár: az érték belső szóközei maradjanak
        matched = False
        for index, segment in enumerate(segments):
            match = _KEY_VALUE.match(segment)
            if not match:
                continue
            matched = True
            key = " ".join(match.group(1).lower().replace(".", " ").split())
            value = " ".join(match.group(2).split())
            # Az összefoglaló „Subtitles/Audio: …” sor mindig globális.
            if key in _SUBTITLE_KEYS:
                continued = ["subtitles", value]
                global_values.append(continued)
                continue
            if columns and index < len(columns):
                columns[index].values.setdefault(key, value)
                continued = None
            else:
                entry = [key, value]
                global_values.append(entry)
                continued = entry if key in _AUDIO_KEYS else None
        if not matched and continued is not None and _languages(line):
            continued[1] += "\n" + " ".join(line.split())

    info = NfoInfo()
    for section in sections:
        values = section.values
        if section.kind == "audio":
            language = _language(values.get("language", ""), allow_codes=True)
            if language:
                info.audio.append(
                    AudioTrack(
                        language=language,
                        channels=_channels(
                            values.get("channels", "")
                            or values.get("channel(s)", "")
                            or values.get("format", "")
                        ),
                        format=values.get("format", "").split()[0]
                        if values.get("format")
                        else "",
                    )
                )
        elif section.kind == "text":
            language = _language(values.get("language", ""), allow_codes=True)
            if language:
                kinds = list(_subtitle_kinds(values.get("title", "")))
                forced = values.get("forced", "").lower() == "yes"
                if forced and "kényszerített" not in kinds:
                    kinds.insert(0, "kényszerített")
                _add_subtitle(info, SubtitleTrack(language, tuple(kinds)))
        elif section.kind in ("video", "general") and not info.bitrate_kbps:
            for key in ("bit rate", "bitrate", "overall bit rate"):
                if key in values and section.kind == "video":
                    info.bitrate_kbps = _bitrate_kbps(values[key])
                    break

    for key, value in global_values:
        if key == "subtitles":
            for part in re.split(r"[\n,;]", value):
                kinds = _subtitle_kinds(part)
                # „Hungarian + Forced”: a teljes mellett kényszerített is van.
                if (
                    "kényszerített" in kinds
                    and "teljes" not in kinds
                    and re.search(r"[+&]|\band\b", part)
                ):
                    kinds = (*kinds, "teljes")
                for language in _languages(part):
                    _add_subtitle(info, SubtitleTrack(language, kinds))
        elif key in _AUDIO_KEYS:
            if not info.audio:
                _parse_audio_summary(info, value)
        elif key in ("bitrate", "bit rate", "video bitrate", "video bit rate"):
            if not info.bitrate_kbps:
                info.bitrate_kbps = _bitrate_kbps(value)

    if not info.bitrate_kbps:
        for section in sections:
            if section.kind == "general" and "overall bit rate" in section.values:
                info.bitrate_kbps = _bitrate_kbps(section.values["overall bit rate"])
    return info


def _parse_audio_summary(info: NfoInfo, value: str) -> None:
    """„Hungarian DD 5.1 / English DTS 5.1” vagy soronként egy sáv."""
    for part in re.split(r"\n|\s/\s|[,;&]|\s\+\s|\band\b", value):
        language = _language(part, allow_codes=False)
        channels = _channels(part)
        if language is None:
            # „Hungarian / DD 5.1”: a csatorna az előző nyelvhez tartozik.
            if channels and info.audio and not info.audio[-1].channels:
                last = info.audio[-1]
                info.audio[-1] = AudioTrack(last.language, channels, last.format)
            continue
        if all(track.language != language for track in info.audio):
            info.audio.append(AudioTrack(language, channels))


def _add_subtitle(info: NfoInfo, track: SubtitleTrack) -> None:
    for index, existing in enumerate(info.subtitles):
        if existing.language == track.language:
            kinds = tuple(dict.fromkeys(existing.kinds + track.kinds))
            # Ha van külön kényszerített és sima sáv, a sima a teljes.
            if track.kinds != existing.kinds and "teljes" not in kinds:
                kinds = (*kinds, "teljes")
            info.subtitles[index] = SubtitleTrack(track.language, kinds)
            return
    info.subtitles.append(track)
