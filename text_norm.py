"""Shared name-normalisation used by dedup.py and osm.py."""
from __future__ import annotations
import re
import unicodedata as _ud

# Cyrillic → Latin transliteration (Russian / Ukrainian)
_CYR: dict[str, str] = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh',
    'з':'z','и':'i','й':'j','к':'k','л':'l','м':'m','н':'n','о':'o',
    'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts',
    'ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'i','ь':'','э':'e','ю':'yu',
    'я':'ya', 'і':'i','ї':'yi','є':'ye','ґ':'g',
}

# Legal entity suffixes
_LEGAL = re.compile(
    r"\b(srl|sa|ooo|llc|ltd|inc|gmbh|is|im|ia|bc|pjsc|ong|gr|cs"
    r"|ооо|оао|зао|ип|чп)\b",
    re.IGNORECASE,
)

# Generic business-type stop words; longer phrases matched first
_STOPS = re.compile(
    r"\b(?:"
    r"beauty\s+salon|nail\s+studio|car\s+wash|shopping\s+malldova|shopping\s+mall"
    r"|spalatorie\s+auto|service\s+auto|salon\s+de\s+frumusete"
    r"|malldova|shopping|mall|centru|center|centre|plaza"
    r"|stomatologie|stomatolog"
    r"|beauty|salon|nail|nails|studio"
    r"|spalatorie|vulcanizare"
    r"|patisserie|patiserie"
    r")\b",
    re.IGNORECASE,
)

_STRIP = re.compile(r"[^\w\s]")
_WS    = re.compile(r"\s+")


def norm(s: str | None) -> str:
    """Normalise a business name for fuzzy comparison."""
    if not s:
        return ""
    s = _ud.normalize("NFKD", s)
    s = "".join(c for c in s if not _ud.combining(c))
    s = s.lower()
    s = "".join(_CYR.get(c, c) for c in s)
    s = _LEGAL.sub(" ", s)
    stripped = _STOPS.sub(" ", s)
    stripped = _STRIP.sub(" ", stripped)
    stripped = _WS.sub(" ", stripped).strip()
    if not stripped:
        s = _STRIP.sub(" ", s)
        return _WS.sub(" ", s).strip()
    return stripped


def similarity(na: str, nb: str) -> tuple[float, float]:
    """Return (token_set_ratio, len_ratio) for two already-normalised strings."""
    from rapidfuzz import fuzz
    tsr = fuzz.token_set_ratio(na, nb)
    if not na or not nb:
        return tsr, 0.0
    lr = min(len(na), len(nb)) / max(len(na), len(nb))
    return tsr, lr
