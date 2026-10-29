"""Allow existing Blizzard icon punctuation without admitting paths or control characters."""
import re

_NAME = re.compile(r'[A-Za-z0-9_&\[\]-]{1,200}\Z')


def safe_icon_name(value):
    return isinstance(value, str) and _NAME.fullmatch(value) is not None
