from __future__ import annotations

import re
import unicodedata

APOSTROPHE_CONTRACTION = re.compile(r"'(S|T|Re|Ve|Ll|D|M)\b")


def standardize_name(value: str) -> str:
    """Return a compact, Unicode-normalized title-cased display name."""

    compact = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
    titled = compact.lower().title()
    return APOSTROPHE_CONTRACTION.sub(lambda match: f"'{match.group(1).lower()}", titled)
