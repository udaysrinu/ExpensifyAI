"""Self-deriving statement passwords from the rule the bank STATES in its email.

Banks tell you the password formula — either in the email text ("...last five digits of the
registered mobile number and DOB in DDMMYY format...") or in an image (RBL's screenshot). Rather
than hard-code every bank, this module reads that stated rule and derives the exact password. It
feeds pdf_statement.password_candidates() as the highest-priority candidate; the brute-force
name/DOB combos remain as the fallback when no rule is found.

Design (SOLID):
- A rule is an ordered list of typed COMPONENTS (mobile[-5:], name[:4].upper(), dob:ddmmyy, ...).
  `parse_password_rule(text)` is pure: rule-text -> [components] in the order they appear.
- `derive_passwords(components, hints)` is pure: components + known facts -> ordered passwords.
- Image rules: `ocr_image(bytes)` (lazy pytesseract fallback) OR the agent reads the image via
  vision and passes the transcribed text to `parse_password_rule`. Either way the text path is
  the single source of truth — Open/Closed: add a bank's phrasing = add a pattern, nothing else.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# spelled-out numbers banks use ("last five digits", "first four letters")
_NUMWORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _num(word: str) -> Optional[int]:
    word = word.strip().lower()
    if word.isdigit():
        return int(word)
    return _NUMWORDS.get(word)


# DOB format tokens, longest-first so "ddmmyyyy" wins over the "ddmm"/"ddmmyy" substrings.
_DOB_FORMATS = ["ddmmyyyy", "mmddyyyy", "ddmmyy", "mmddyy", "ddmm", "mmyy", "yyyy"]


def parse_password_rule(text: str) -> List[Dict[str, Any]]:
    """Parse a stated password rule into ordered components. [] if no rule recognized.

    Recognizes the common Indian-bank phrasings, e.g.:
      SBI:  'last five digits of ... mobile number and date of birth (DOB) in DDMMYY format'
      RBL:  'first four letters of your name in CAPITALS followed by DOB in DDMMYY'
      HDFC: 'first 4 characters of your name and date of birth in DDMM'
    Component dicts: {kind:'mobile'|'card', take:N} | {kind:'name', take:N, case:'upper'|'lower'|'any'}
      | {kind:'dob', fmt:'ddmmyy'|...}. Ordered by their position in the sentence.
    """
    if not text:
        return []
    tl = text.lower()
    found: List[tuple] = []  # (position, component)

    # mobile: "last <N> digits of ... mobile"
    for m in re.finditer(r"last\s+(\w+)\s+digits?\s+of\s+.{0,60}?mobile", tl):
        n = _num(m.group(1))
        if n:
            found.append((m.start(), {"kind": "mobile", "take": n}))
    # card: "last <N> digits of ... card"
    for m in re.finditer(r"last\s+(\w+)\s+digits?\s+of\s+.{0,60}?card", tl):
        n = _num(m.group(1))
        if n:
            found.append((m.start(), {"kind": "card", "take": n}))
    # name: "first <N> letters/characters of ... name" (+ optional CAPITALS/lower nearby)
    for m in re.finditer(
        r"first\s+(\w+)\s+(?:letters?|characters?|chars?)\s+of\s+(?:the\s+|your\s+|card\s?holder'?s?\s+|account\s?holder'?s?\s+)*name",
        tl,
    ):
        n = _num(m.group(1))
        if not n:
            continue
        window = tl[m.start(): m.end() + 40]
        if re.search(r"capital|caps\b|block\s+letter|upper", window):
            case = "upper"
        elif "lower" in window or "small" in window:
            case = "lower"
        else:
            case = "any"
        found.append((m.start(), {"kind": "name", "take": n, "case": case}))
    # dob: pick the format token that actually appears (longest match first)
    dob_pos = None
    dob_fmt = None
    for fmt in _DOB_FORMATS:
        m = re.search(r"\b" + fmt + r"\b", tl)
        if m and (dob_pos is None or m.start() < dob_pos):
            # prefer the longer format if two start at the same spot
            if dob_fmt is None or len(fmt) > len(dob_fmt) or m.start() < dob_pos:
                dob_pos, dob_fmt = m.start(), fmt
    if dob_fmt:
        found.append((dob_pos, {"kind": "dob", "fmt": dob_fmt}))

    found.sort(key=lambda x: x[0])
    return [c for _, c in found]


def _dob_part(dob_digits: str, fmt: str) -> str:
    d = dob_digits
    if len(d) < 4:
        return ""
    # normalize to DDMMYYYY when possible for slicing
    dd, mm = d[:2], d[2:4]
    yyyy = d[4:8] if len(d) >= 8 else ""
    yy = yyyy[2:] if yyyy else ""
    return {
        "ddmm": dd + mm,
        "mmdd": mm + dd,
        "ddmmyy": dd + mm + yy,
        "mmddyy": mm + dd + yy,
        "ddmmyyyy": dd + mm + yyyy,
        "mmddyyyy": mm + dd + yyyy,
        "mmyy": mm + yy,
        "yyyy": yyyy,
    }.get(fmt, "")


def derive_passwords(components: List[Dict[str, Any]], hints: Dict[str, Any]) -> List[str]:
    """Apply parsed components to known facts -> ordered candidate passwords (best first).

    hints: name, dob ('DDMMYYYY'/'DD-MM-YYYY'/...), mobile, card_last4. Returns [] if a required
    component can't be filled (e.g. rule needs mobile but none provided) — so the caller falls back
    to brute-force candidates. When a name's case is unstated we emit upper/lower/capitalize variants.
    """
    if not components:
        return []
    name = re.sub(r"[^A-Za-z]", "", str(hints.get("name") or ""))
    dob_digits = re.sub(r"\D", "", str(hints.get("dob") or ""))
    mobile = re.sub(r"\D", "", str(hints.get("mobile") or ""))
    card = re.sub(r"\D", "", str(hints.get("card_last4") or ""))

    # each component -> list of string options (usually 1; name-with-unknown-case -> 3)
    per_part: List[List[str]] = []
    for c in components:
        kind = c["kind"]
        if kind == "mobile":
            if len(mobile) < c["take"]:
                return []
            per_part.append([mobile[-c["take"]:]])
        elif kind == "card":
            if len(card) < c["take"]:
                return []
            per_part.append([card[-c["take"]:]])
        elif kind == "name":
            if len(name) < c["take"]:
                return []
            base = name[: c["take"]]
            if c["case"] == "upper":
                per_part.append([base.upper()])
            elif c["case"] == "lower":
                per_part.append([base.lower()])
            else:  # unknown case -> try common variants, upper first (most banks)
                per_part.append([base.upper(), base.lower(), base.capitalize()])
        elif kind == "dob":
            part = _dob_part(dob_digits, c["fmt"])
            if not part:
                return []
            per_part.append([part])

    # cartesian product of the per-part options, preserving order
    out: List[str] = [""]
    for options in per_part:
        out = [prefix + opt for prefix in out for opt in options]
    # dedupe, keep order
    seen, result = set(), []
    for pw in out:
        if pw and pw not in seen:
            seen.add(pw)
            result.append(pw)
    return result


def passwords_from_rule_text(text: str, hints: Dict[str, Any]) -> List[str]:
    """Convenience: rule text -> derived passwords (best first). [] if unrecognized."""
    return derive_passwords(parse_password_rule(text), hints)


def ocr_image(image_bytes: bytes) -> str:
    """OCR fallback for password-rule images (RBL-style). Lazy pytesseract; agent-vision preferred.

    Raises a clear, actionable error if pytesseract/Pillow/tesseract aren't available, so the
    caller can fall back to agent vision instead of crashing.
    """
    try:
        import io
        from PIL import Image
        import pytesseract
    except ImportError as e:
        raise RuntimeError(
            "OCR fallback needs Pillow + pytesseract and the tesseract binary "
            "(pip install pytesseract pillow; brew install tesseract). "
            "Prefer agent vision: read the image and pass its text to parse_password_rule()."
        ) from e
    try:
        import io
        img = Image.open(io.BytesIO(image_bytes))
        return pytesseract.image_to_string(img)
    except Exception as e:  # tesseract binary missing / unreadable image
        raise RuntimeError(f"OCR failed ({e}). Use agent vision on the image instead.") from e
