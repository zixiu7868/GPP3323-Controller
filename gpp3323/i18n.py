"""Explicit UI translations. Language is fixed for an application session."""
import json
from pathlib import Path

_language = 'zh-TW'
_english = json.loads(Path(__file__).with_name('en.json').read_text(encoding='utf-8'))


def set_language(language: str) -> None:
    global _language
    _language = language if language in ('zh-TW', 'en') else 'zh-TW'


def tr(message: str, *values) -> str:
    translated = _english.get(message, message) if _language == 'en' else message
    return translated.format(*values) if values else translated
