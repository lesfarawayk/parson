"""Parse torrent titles in format [Studio]Film Name[Tags/Genres/Formats][Devices].

Separates video formats from genre tags, normalizes and deduplicates formats
so that e.g. '4K' and '2160p' in the same title count as one format.
"""

import re

# Canonical format → list of aliases (all lowercase)
FORMAT_GROUPS = [
    ("8K",    ["8k", "4320p", "7680x4320"]),
    ("4K",    ["4k", "2160p", "3840x2160", "uhd"]),
    ("2K",    ["2k", "1440p", "2560x1440", "qhd"]),
    ("1080p", ["1080p", "fhd", "1920x1080"]),
    ("720p",  ["720p", "1280x720"]),
    ("480p",  ["480p"]),
    ("360p",  ["360p"]),
    ("VR",    ["vr"]),
    # Non-standard resolutions from user config
    ("3080p", ["3080p"]),
    ("3000p", ["3000p"]),
]

# Build lookup: alias(lower) → canonical name
FORMAT_ALIASES: dict[str, str] = {}
for canonical, aliases in FORMAT_GROUPS:
    for alias in aliases:
        FORMAT_ALIASES[alias] = canonical

ALL_FORMAT_TOKENS = set(FORMAT_ALIASES.keys())


def normalize_format(fmt: str) -> str:
    """Map any format alias to its canonical name. Unknown formats kept as-is."""
    return FORMAT_ALIASES.get(fmt.lower().strip(), fmt.strip())


def deduplicate_formats(formats: list[str]) -> list[str]:
    """Deduplicate after normalization: ['4K', '2160p'] → ['4K']."""
    seen: set[str] = set()
    result = []
    for fmt in formats:
        canonical = normalize_format(fmt)
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def parse_title(title: str) -> dict:
    """
    Parse: [Studio]Film Name[Tags, Genres, Formats][Devices]

    Returns {studio, film_name, formats, tags, devices}.
    """
    # Split by [...] groups
    parts = re.split(r'\[([^\]]*)\]', title)
    # parts = [text0, bracket1, text1, bracket2, text2, ...]

    bracket_contents = []
    text_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            text_parts.append(part.strip())
        else:
            bracket_contents.append(part.strip())

    studio = bracket_contents[0] if bracket_contents else ""

    # Film name = text after first ], before next [
    film_name = text_parts[1] if len(text_parts) > 1 else (text_parts[0] if text_parts else "")
    film_name = film_name.strip(" -\u2013\u2014/")

    # All remaining bracket groups contain tags/formats/devices
    tag_groups = bracket_contents[1:] if len(bracket_contents) > 1 else []

    # Tokenize all bracket groups
    all_tokens = []
    for group in tag_groups:
        tokens = re.split(r'[,/;|]', group)
        all_tokens.extend([t.strip() for t in tokens if t.strip()])

    # Separate formats from genre/tags
    formats = []
    genres = []
    for token in all_tokens:
        token_lower = token.lower().strip()
        if token_lower in ALL_FORMAT_TOKENS:
            formats.append(token)
        elif re.match(r'^\d{3,5}p$', token_lower) or re.match(r'^\d{3,5}x\d{3,5}$', token_lower):
            # Looks like a resolution even if not in our list
            formats.append(token)
        else:
            genres.append(token)

    formats = deduplicate_formats(formats)

    # Detect devices in the last bracket group
    devices = []
    device_keywords = {
        "vr", "oculus", "gear vr", "psvr", "htc vive", "valve index",
        "quest", "rift", "cardboard", "daydream", "pimax", "go",
        "smartphone", "tablet", "ps4", "ps5", "xbox",
    }
    if len(bracket_contents) > 2:
        last_group = bracket_contents[-1]
        device_tokens = [t.strip() for t in re.split(r'[,/;|]', last_group) if t.strip()]
        if any(t.lower() in device_keywords for t in device_tokens):
            devices = device_tokens
            device_lower = {t.lower() for t in device_tokens}
            genres = [g for g in genres if g.lower() not in device_lower]

    return {
        "studio": studio,
        "film_name": film_name,
        "formats": formats,
        "tags": genres,
        "devices": devices,
    }


def matches_format_filter(formats: list[str], filter_formats: list[str]) -> bool:
    """Check if any parsed format matches the configured filter list."""
    if not filter_formats:
        return True  # no filter = accept all
    normalized_filters = {normalize_format(f) for f in filter_formats}
    normalized_parsed = {normalize_format(f) for f in formats}
    return bool(normalized_filters & normalized_parsed)
