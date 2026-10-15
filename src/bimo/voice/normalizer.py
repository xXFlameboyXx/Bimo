"""Phonetic normalization and wake-word verification engine for Bimo.

Provides:
1. Phonetic alias resolution for "Bimo" across diverse accents and speech recognizers
   (resolves "Be more", "Beam o", "Bemo", "Veemo", "Pimo", Indian/Hindi phonetics).
2. Wake-word detection and gating: enforces that Bimo only processes speech
   containing recognized wake words.
3. Command extraction: cleanly strips wake-word invocations while retaining the user intent.
"""

from __future__ import annotations

from collections.abc import Sequence
import logging
import re
from typing import Final

logger = logging.getLogger(__name__)

# Canonical robot name
ROBOT_NAME: Final[str] = "Bimo"

# Default secondary / hardware wake words
DEFAULT_SECONDARY_WAKE_WORDS: Final[tuple[str, ...]] = (
    "hey_jarvis",
    "hey jarvis",
    "jarvis",
    "alexa",
    "hey_mycroft",
    "hey mycroft",
    "computer",
)

# Comprehensive phonetic mappings for "Bimo" and common misheard vocalizations
BIMO_PHONETIC_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Explicit "Hey Bimo" mishearings (e.g. "maybe more", "heavy more", "baby more", "heavier more", "hey be more", "hey vivo")
    (re.compile(r"\b(?:[Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+(?:[Mm]aybe\s+more|[Mm]aybe-more|[Mm]aybemore|[Mm]aybe\s+mo|[Mm]ay\s+be\s+more)\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Mm]aybe\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Mm]aybe-more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Mm]aybemore\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Mm]aybe\s+mo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Mm]ay\s+be\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b(?:[Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+(?:[Bb]aby\s+more|[Bb]aby-more|[Bb]abymore|[Bb]aby\s+mo|[Bb]aby\s+mode)\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Bb]aby\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Bb]aby-more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Bb]abymore\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Bb]aby\s+mo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Bb]aby\s+mode\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b(?:[Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+(?:[Hh]eavy\s+more|[Hh]eavy-more|[Hh]eavymore|[Hh]eavi\s*more|[Hh]eavy\s+mo|[Hh]eavier\s+more|[Hh]eaven\s+more|[Hh]ave\s+a\s+more)\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]eavy\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]eavy-more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]eavymore\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]eavi\s*more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]eavy\s+mo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]eavier\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]eaven\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ave\s+a\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"^[Hh]ave\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"^[Hh]eavy\s+mode\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"^[Hh]eavy\s+move\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+[Hh]eavy\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+be\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+bee\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+be-more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+be\s+mo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+beam\s*o\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+beam\s*oh\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+vivo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+vevo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+vimo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+veemo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+weemo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+bemo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+beemo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+pimo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+memo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ey\s+remote\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ay\s+bimo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ay\s+be\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Hh]ay\s+vivo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Aa]e\s+bimo\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Aa]e\s+be\s+more\b", re.IGNORECASE), "Hey Bimo"),
    (re.compile(r"\b[Aa]e\s+vivo\b", re.IGNORECASE), "Hey Bimo"),
    # Compound / multi-word mishearings
    (re.compile(r"\b[Bb]e\s+more\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]ee\s+more\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]e-more\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eemore\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]e\s+mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]ee\s+mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eemo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eam\s*o\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eam\s*oh\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eamo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eam\s*out\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]emo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]-mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]\s+mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]\.\s*mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]\.\s*Mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]\s+me\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]i\s+mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]\s+imo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]y\s+mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]uy\s+mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]e\s+mode\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]e\s+mood\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]e\s+move\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]eemo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]imo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]-mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eimo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Pp]imo\b", re.IGNORECASE), "Bimo"),
    # Vivo / Vevo / Viva / Vino / Weemo phonetics (routine Indian English STT mishearings)
    (re.compile(r"\b[Vv]ivo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]i-vo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]i\s+vo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]ee\s*vo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]evo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]e-vo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]iva\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]ino\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Ww]eemo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Ww]ee\s*mo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Ww]imo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]ibo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]ebo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eebo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Dd]eemo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Dd]imo\b", re.IGNORECASE), "Bimo"),
    # Clipped speech onset / standalone "remove" (e.g. STT hearing "remove" for "Bimo")
    (re.compile(r"^[Rr]emove[\.?!]*$", re.IGNORECASE), "Bimo"),
    (
        re.compile(
            r"^[Rr]emove[\s,]+(?=(?:can\s+you|could\s+you|please|what|who|where|how|why|open|close|start|stop|help|listen|are\s+you|hello|hi|namaste)\b)",
            re.IGNORECASE,
        ),
        "Bimo ",
    ),
    (
        re.compile(
            r"\b([Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+[Rr]emove\b",
            re.IGNORECASE,
        ),
        r"\1 Bimo",
    ),
    # Indian / Hindi phonetics for Bimo / Bheem
    (re.compile(r"\b[Bb]heem\s*o\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]heemo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]him\s*o\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]himo\b", re.IGNORECASE), "Bimo"),
    # Demo as a vocative prefix (e.g. "Demo open notepad", "Hey demo")
    (re.compile(r"^[Dd]emo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Hh]ey\s+[Dd]emo\b", re.IGNORECASE), "Hey Bimo"),
    # Bimu / Beemu phonetics
    (re.compile(r"\b[Bb]imu\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]eemu\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Bb]emu\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]imu\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Vv]eemu\b", re.IGNORECASE), "Bimo"),
    # Memo vocative / onset mishearings (e.g. "hi memo how are you", "Memo open notepad")
    (re.compile(r"\b([Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+[Mm]emo\b", re.IGNORECASE), r"\1 Bimo"),
    (re.compile(r"\b([Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+[Mm]eemo\b", re.IGNORECASE), r"\1 Bimo"),
    (re.compile(r"\b([Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+[Mm]immo\b", re.IGNORECASE), r"\1 Bimo"),
    (re.compile(r"^[Mm]emo[\.?!]*$", re.IGNORECASE), "Bimo"),
    (
        re.compile(
            r"^[Mm]emo[\s,]+(?=(?:can\s+you|could\s+you|please|what|who|where|how|why|open|close|start|stop|help|listen|are\s+you|hello|hi|namaste|tell|show|run|play)\b)",
            re.IGNORECASE,
        ),
        "Bimo ",
    ),
    # Remo / Reemo mishearings for "Bimo"
    (re.compile(r"\b([Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+[Rr]eemo\b", re.IGNORECASE), r"\1 Bimo"),
    (re.compile(r"\b([Hh]ey|[Oo][Kk]|[Oo]kay|[Hh]i|[Hh]ello|[Ss]uno)\s+[Rr]emo\b", re.IGNORECASE), r"\1 Bimo"),
    (re.compile(r"\b[Rr]eemo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Rr]emo\b", re.IGNORECASE), "Bimo"),
    (re.compile(r"\b[Rr]aymo\b", re.IGNORECASE), "Bimo"),
)

# Common Indian / Hindi and user context corrections
GENERAL_VOCABULARY_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\b[Pp]an+e+r\b", re.IGNORECASE), "paneer"),
    (re.compile(r"\b[Nn]umb\s*state\b", re.IGNORECASE), "namaste"),
    (re.compile(r"\b[Nn]um\s*stay\b", re.IGNORECASE), "namaste"),
    (re.compile(r"\b[Gg]un\s+chalao\b", re.IGNORECASE), "gaana chalao"),
    (re.compile(r"\b[Gg]anna\s+chalao\b", re.IGNORECASE), "gaana chalao"),
    (re.compile(r"\b[Gg]ana\s+chalao\b", re.IGNORECASE), "gaana chalao"),
    (re.compile(r"\b[Bb]andh?\s+karo\b", re.IGNORECASE), "band karo"),
    (re.compile(r"\bepisode\s+song\b", re.IGNORECASE), "despacito"),
    (re.compile(r"\bpsi\s+song\b", re.IGNORECASE), "PSY song"),
)

# Regex to detect any Bimo wake word invocation in text
WAKE_WORD_DETECTION_REGEX = re.compile(
    r"\b("
    r"bimo|beemo|bemo|veemo|vimo|beimo|pimo|bimu|beemu|bemu|"
    r"vivo|vevo|viva|vino|weemo|wimo|bibo|bebo|beebo|deemo|dimo|"
    r"heavy\s+more|heavy-more|heavymore|heavy\s+mo|heavier\s+more|heaven\s+more|"
    r"baby\s+more|baby-more|babymore|baby\s+mo|baby\s+mode|"
    r"maybe\s+more|maybe-more|maybemore|maybe\s+mo|may\s+be\s+more|"
    r"have\s+a\s+more|"
    r"remo|reemo|raymo|"
    r"be\s+more|bee\s+more|be-more|beemore|"
    r"beam\s*o|beam\s*oh|beamo|beam\s*out|"
    r"be\s+mo|bee\s+mo|b\s+mo|b-mo|b\.\s*mo|"
    r"bheem\s*o|bheemo|bhim\s*o|bhimo|"
    r"(?:hey|ok|okay|hi|hello|suno)\s+memo|"
    r"hey\s+jarvis|jarvis|alexa|hey\s+mycroft|computer"
    r")\b",
    re.IGNORECASE,
)


def normalize_phonetic_text(text: str, custom_replacements: dict[str, str] | None = None) -> str:
    """Normalize phonetic mishearings in transcribed speech.

    Transforms variations like "Be more", "Beam o", "Bemo", "Veemo", and accented
    speech into canonical "Bimo", while preserving the rest of the text.

    Args:
        text: Raw transcribed speech string.
        custom_replacements: Optional dict of pattern -> replacement strings.

    Returns:
        Cleaned and normalized speech string.
    """
    if not text:
        return ""

    result = text.strip()

    # 1. Apply Bimo phonetic normalizations
    for pattern, replacement in BIMO_PHONETIC_PATTERNS:
        result = pattern.sub(replacement, result)

    # 2. Apply general Indian-English / vocabulary patterns
    for pattern, replacement in GENERAL_VOCABULARY_PATTERNS:
        result = pattern.sub(replacement, result)

    # 3. Apply custom user replacements if provided
    if custom_replacements:
        for pat_str, repl in custom_replacements.items():
            result = re.sub(pat_str, repl, result, flags=re.IGNORECASE)

    # Ensure canonical capitalization of Bimo and deduplicate prefixes (e.g. "Hey Hey Bimo" -> "Hey Bimo")
    result = re.sub(r"\b(?:hey\s+)+bimo\b", "Hey Bimo", result, flags=re.IGNORECASE)
    result = re.sub(r"\b(?:ok\s+|okay\s+)+(?:ok\s+|okay\s+)*bimo\b", "OK Bimo", result, flags=re.IGNORECASE)
    result = re.sub(r"\bbimo\b", "Bimo", result, flags=re.IGNORECASE)

    return result.strip()


def contains_wake_word(
    text: str,
    configured_models: Sequence[str] | None = None,
) -> tuple[bool, str | None]:
    """Check whether transcribed speech contains an accepted wake word or phonetic alias.

    Evaluates both raw speech and normalized speech against:
    - "Bimo" and all phonetic variants ("Be more", "Beam o", "Bemo", etc.)
    - Configured secondary wake words (e.g. "alexa", "hey_jarvis", "hey_mycroft")

    Args:
        text: Transcribed speech to evaluate.
        configured_models: Optional list of configured wake-word model names.

    Returns:
        (True, matched_wake_word) if a wake word was detected,
        (False, None) if no wake word was found.
    """
    if not text or not text.strip():
        return False, None

    clean = text.strip()

    # 1. Fast regex search across all Bimo and standard aliases
    match = WAKE_WORD_DETECTION_REGEX.search(clean)
    if match:
        matched_str = match.group(0).strip()
        # Map phonetic variant back to canonical
        normalized_str = normalize_phonetic_text(matched_str)
        return True, normalized_str if normalized_str else "Bimo"

    # 2. Check normalized text directly
    normalized = normalize_phonetic_text(clean)
    if re.search(r"\bBimo\b", normalized, re.IGNORECASE):
        return True, "Bimo"

    # 3. Check against any custom configured models (e.g. "alexa", "hey_jarvis")
    if configured_models:
        for model in configured_models:
            clean_model = model.replace("_", " ").strip()
            if clean_model and re.search(rf"\b{re.escape(clean_model)}\b", clean, re.IGNORECASE):
                return True, clean_model

    return False, None


def extract_command_after_wake_word(text: str) -> str:
    """Extract the intent / command portion of an utterance after the wake word.

    Examples:
        - "Bimo, open notepad" -> "open notepad"
        - "Hey Bimo can you open calculator" -> "can you open calculator"
        - "Be more, what time is it?" -> "what time is it?"
        - "Bimo" -> "Bimo"

    Args:
        text: Transcribed utterance containing a wake word.

    Returns:
        The extracted command, or the original text if only the wake word was present.
    """
    if not text:
        return ""

    # First normalize so the wake word is consistently "Bimo" or "Hey Bimo"
    normalized = normalize_phonetic_text(text)

    # Patterns where wake word is at or near the beginning
    # Examples: "Bimo, ...", "Hey Bimo, ...", "OK Bimo, ...", "Alexa, ..."
    prefix_patterns = (
        r"^(?:hey\s+|ok\s+|okay\s+|hi\s+|hello\s+|suno\s+)?bimo[\s,:\-!\.]*",
        r"^(?:hey\s+|ok\s+|okay\s+)?jarvis[\s,:\-!\.]*",
        r"^alexa[\s,:\-!\.]*",
        r"^hey\s+mycroft[\s,:\-!\.]*",
        r"^computer[\s,:\-!\.]*",
    )

    for pat in prefix_patterns:
        stripped = re.sub(pat, "", normalized, flags=re.IGNORECASE).strip()
        if stripped:
            return stripped

    # If the user only said "Bimo" or "Hey Bimo" with no subsequent command,
    # return normalized string
    return normalized
