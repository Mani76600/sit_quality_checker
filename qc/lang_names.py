"""ISO 639-1-ish locale code -> full English language name, for display
only (e.g. the report's generation-info strip shows "EN - English" instead
of a bare "en"). Not exhaustive - covers common real-pipeline locales;
an unrecognized code is shown as-is (uppercased) rather than failing.
"""

from __future__ import annotations

CODE_TO_NAME = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish",
    "da": "Danish", "no": "Norwegian", "fi": "Finnish", "pl": "Polish",
    "ru": "Russian", "uk": "Ukrainian", "bg": "Bulgarian", "ro": "Romanian",
    "hu": "Hungarian", "cs": "Czech", "sk": "Slovak", "sl": "Slovenian",
    "hr": "Croatian", "sr": "Serbian", "et": "Estonian", "lv": "Latvian",
    "lt": "Lithuanian", "el": "Greek", "tr": "Turkish", "he": "Hebrew",
    "ar": "Arabic", "hi": "Hindi", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "ms": "Malay", "zh": "Chinese", "zh-hans": "Chinese (Simplified)",
    "zh-hant": "Chinese (Traditional)", "ja": "Japanese", "ko": "Korean",
    "af": "Afrikaans", "sw": "Swahili", "zu": "Zulu", "fa": "Persian",
    "ur": "Urdu", "bn": "Bengali", "ta": "Tamil", "te": "Telugu",
    "mr": "Marathi", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam",
    "pa": "Punjabi", "am": "Amharic", "ka": "Georgian", "hy": "Armenian",
    "az": "Azerbaijani", "kk": "Kazakh", "uz": "Uzbek", "mn": "Mongolian",
    "ne": "Nepali", "si": "Sinhala", "km": "Khmer", "lo": "Lao",
    "my": "Burmese", "is": "Icelandic", "ga": "Irish", "cy": "Welsh",
    "mt": "Maltese", "sq": "Albanian", "mk": "Macedonian", "bs": "Bosnian",
}


def full_name(code: str) -> str:
    return CODE_TO_NAME.get(code.strip().lower(), code.strip())


def display_form(code: str) -> str:
    """"en" -> "EN - English"; an unrecognized code -> just itself, uppercased."""
    code = code.strip()
    name = CODE_TO_NAME.get(code.lower())
    return f"{code.upper()} - {name}" if name else code.upper()
