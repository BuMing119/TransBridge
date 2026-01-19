import logging
import re
import unicodedata

log = logging.getLogger("TextCleaning")


def clean_string(text: str) -> str:
    """
    Clean string by removing control characters and fixing encoding issues.

    Cleaning operations:
    - Remove ASCII control characters (except newline, tab, carriage return)
    - Remove zero-width spaces and other invisible Unicode
    - Normalize Unicode to NFC form

    Args:
        text: Original text to clean

    Returns:
        Cleaned text
    """
    if not text:
        return text

    # Remove control characters (0x00-0x1F) except \n, \r, \t
    cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)

    # Remove zero-width spaces and similar
    cleaned = cleaned.replace("\u200b", "")  # Zero-width space
    cleaned = cleaned.replace("\ufeff", "")  # BOM

    # Normalize Unicode
    cleaned = unicodedata.normalize("NFC", cleaned)

    # Log if cleaning made changes
    if cleaned != text:
        log.info(f"Cleaned string: '{text[:50]}...' -> '{cleaned[:50]}...'")

    return cleaned
