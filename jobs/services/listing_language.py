"""Cheap language guess for listings, so non-English roles can be labelled.

Counts common function words in the title and the start of the description. Good
enough to label a German or French posting on an English-language board; not a
general language detector.
"""
import re

_MARKERS = {
    "German": re.compile(r"\b(und|der|die|das|wir|für|mit|bei|sie|ihre|unsere|oder)\b", re.I),
    "French": re.compile(r"\b(nous|vous|avec|pour|les|des|une|dans|est|sont|notre)\b", re.I),
    "Spanish": re.compile(r"\b(para|con|del|las|los|que|una|por|nuestro|equipo)\b", re.I),
}
_ENGLISH = re.compile(r"\b(the|and|you|with|for|our|will|are|this|your)\b", re.I)
_GENDER_TAG = re.compile(r"\((?:m|w|f|d|x)/(?:m|w|f|d|x)/(?:m|w|f|d|x)\)|\(all genders\)", re.I)


def listing_language(title, description=""):
    """'German', 'French', 'Spanish', or '' for English/unknown."""
    text = f"{title or ''} {(description or '')[:800]}"
    english = len(_ENGLISH.findall(text))
    best, best_hits = "", 0
    for name, pattern in _MARKERS.items():
        hits = len(pattern.findall(text))
        if hits > best_hits:
            best, best_hits = name, hits
    if best_hits >= 6 and best_hits > english * 1.5:
        return best
    if best == "German" and _GENDER_TAG.search(title or "") and best_hits >= 3 and best_hits > english:
        return best
    return ""
