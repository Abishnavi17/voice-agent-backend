"""phone.py - turn what a web form gives us into an E.164 number Vapi accepts."""

from __future__ import annotations

import re

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw: str, default_country_code: str = "") -> str:
    """Return an E.164 number like '+919876543210' or raise ValueError.

    Accepts spaces, dashes, dots and brackets. A leading '00' becomes '+'.
    A number with no '+' is treated as a national number: a single leading '0'
    (trunk prefix) is dropped and `default_country_code` (e.g. '+91') is added.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("phone number is empty")

    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if text.startswith("00") and not plus:
        plus, digits = True, digits[2:]

    if plus:
        candidate = "+" + digits
    else:
        cc = default_country_code.strip()
        if not cc:
            raise ValueError("phone number must start with + and a country code")
        cc_digits = re.sub(r"\D", "", cc)
        national = digits[1:] if digits.startswith("0") else digits
        candidate = f"+{cc_digits}{national}"

    if not _E164.match(candidate):
        raise ValueError(f"{raw!r} is not a valid international phone number")
    return candidate
