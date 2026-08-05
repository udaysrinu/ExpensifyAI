"""Unlock and parse password-protected bank credit-card statement PDFs (CRED-style).

Bank statement PDF passwords are formulaic, derived from data you already know (name, DOB,
card last-4). This module GENERATES candidate passwords, decrypts with pikepdf, extracts the
transaction table text with pdfplumber, and parses transaction rows — which then feed the
existing statement_import review pipeline (nothing is created here).

Design (SOLID):
- `password_candidates(hints)` — pure: hints -> ordered list of likely passwords. Open for new
  bank formats (add a generator) without touching decrypt/parse.
- `decrypt_pdf(data, passwords)` — single responsibility: try passwords, return decrypted bytes.
- `extract_text(pdf_bytes)` — single responsibility: PDF bytes -> text (pdfplumber).
- `parse_transactions(text)` — pure: statement text -> [{date, description, amount}].
- `unlock_and_parse(data, hints)` — orchestrates the above.
Heavy deps (pikepdf, pdfplumber) are imported lazily so the module imports without them.
"""

from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Password derivation (open for extension: add a bank's generator here)
# ---------------------------------------------------------------------------

def password_candidates(hints: Dict[str, Any]) -> List[str]:
    """Build likely statement-PDF passwords from hints, most-likely first (deduped).

    hints keys (all optional): name (full or first), dob ('DDMMYYYY' or 'DD-MM-YYYY'),
    card_last4, mobile, custom (explicit passwords to try first), rule_text (a password rule the
    bank STATED in its email/image — parsed and derived first, most precise).
    Covers common Indian formats: HDFC (first4 name lower/UPPER + DDMM), ICICI/Axis (name+dob),
    SBI (mobile-last5 + DDMMYY), RBL (NAME4 caps + DDMMYY), etc. `custom` handles anything bespoke.
    """
    out: List[str] = []
    seen = set()

    def add(pw: Optional[str]):
        if pw and pw not in seen:
            seen.add(pw)
            out.append(pw)

    for pw in hints.get("custom") or []:
        add(str(pw))

    # a rule the bank stated (text, or transcribed from an image) -> most precise; try first
    rule_text = hints.get("rule_text")
    if rule_text:
        try:
            from . import password_rule
            for pw in password_rule.passwords_from_rule_text(str(rule_text), hints):
                add(pw)
        except Exception:
            pass  # never let rule parsing block the brute-force fallback below

    name = (hints.get("name") or "").strip()
    # each WORD of the full name yields a first-4 slice — the bank's "first name" may be any
    # word (e.g. surname-first registrations), so try them all. Plus the whole-name first-4.
    words = [w for w in re.split(r"\s+", name) if w]
    slices = []
    for w in words:
        s4 = re.sub(r"[^A-Za-z]", "", w)[:4]
        if s4:
            slices.append(s4)
    whole4 = re.sub(r"[^A-Za-z]", "", name)[:4]
    if whole4:
        slices.append(whole4)

    dob_digits = re.sub(r"\D", "", str(hints.get("dob") or ""))
    ddmm = dob_digits[:4] if len(dob_digits) >= 4 else ""
    mmdd = (dob_digits[2:4] + dob_digits[:2]) if len(dob_digits) >= 4 else ""
    ddmmyyyy = dob_digits[:8] if len(dob_digits) >= 8 else ""
    ddmmyy = (dob_digits[:4] + dob_digits[6:8]) if len(dob_digits) >= 8 else ""  # RBL: DDMMYY
    yyyy = dob_digits[4:8] if len(dob_digits) >= 8 else ""
    yy = dob_digits[6:8] if len(dob_digits) >= 8 else ""
    card4 = re.sub(r"\D", "", str(hints.get("card_last4") or ""))[:4]
    mobile = re.sub(r"\D", "", str(hints.get("mobile") or ""))
    mob5 = mobile[-5:] if len(mobile) >= 5 else ""  # SBI: last 5 digits of registered mobile

    # name-slice (case variants) + DOB orderings — the common HDFC/ICICI/RBL/Axis family
    for s4 in slices:
        for dob_part in (ddmm, ddmmyy, mmdd, ddmmyyyy, yyyy, yy):
            if not dob_part:
                continue
            add(s4.lower() + dob_part)
            add(s4.upper() + dob_part)
            add(s4.capitalize() + dob_part)
    # card last4 + DOB (OneCard style), and simple fallbacks
    for dob_part in (ddmm, mmdd, yyyy, ddmmyyyy):
        if card4 and dob_part:
            add(card4 + dob_part)
            add(dob_part + card4)
    # mobile-last5 + DOB (SBI e-statement style), both orderings + common formats
    for dob_part in (ddmmyy, ddmm, ddmmyyyy):
        if mob5 and dob_part:
            add(mob5 + dob_part)
            add(dob_part + mob5)
    add(card4 or None)
    add(ddmmyyyy or None)
    return out


# ---------------------------------------------------------------------------
# Decrypt / extract / parse (each one responsibility)
# ---------------------------------------------------------------------------

class PdfDecryptError(RuntimeError):
    pass


def decrypt_pdf(data: bytes, passwords: List[str]) -> bytes:
    """Try each password; return decrypted PDF bytes. Raises PdfDecryptError if none work.

    If the PDF isn't encrypted, returns it unchanged.
    """
    try:
        import pikepdf
    except ImportError as e:
        raise PdfDecryptError("pikepdf not installed (pip install pikepdf)") from e

    # already open? not encrypted -> return as-is
    try:
        with pikepdf.open(io.BytesIO(data)):
            return data
    except pikepdf.PasswordError:
        pass

    for pw in passwords:
        try:
            with pikepdf.open(io.BytesIO(data), password=pw) as pdf:
                out = io.BytesIO()
                pdf.save(out)
                return out.getvalue()
        except pikepdf.PasswordError:
            continue
    raise PdfDecryptError(
        f"Could not unlock PDF with {len(passwords)} candidate password(s). "
        "Provide correct hints (name/dob/card_last4) or an explicit password via custom."
    )


def extract_text(pdf_bytes: bytes) -> str:
    """Extract text from a (decrypted) PDF using pdfplumber."""
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError("pdfplumber not installed (pip install pdfplumber)") from e
    parts: List[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


# transaction row patterns commonly seen in Indian CC statements
_TXN_PATTERNS = [
    # 11/07/2026  MERCHANT NAME ...  1,508.00   (trailing amount, dd/mm/yyyy or dd-mm-yyyy)
    re.compile(r"(?P<date>\d{2}[/-]\d{2}[/-]\d{4})\s+(?P<desc>.+?)\s+(?P<amt>[\d,]+\.\d{2})(?:\s*(?P<cr>Cr))?\s*$"),
    # 11 Jul 2026  MERCHANT ...  1508.00
    re.compile(r"(?P<date>\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+(?P<desc>.+?)\s+(?P<amt>[\d,]+\.\d{2})(?:\s*(?P<cr>Cr))?\s*$"),
]


def parse_transactions(text: str) -> List[Dict[str, Any]]:
    """Parse likely transaction rows from statement text -> [{date, description, amount, credit}].

    Credit/payment rows (marked 'Cr') are flagged so the caller can skip refunds/payments.
    Best-effort: statement layouts vary; the agent should sanity-check against the summary total.
    """
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pat in _TXN_PATTERNS:
            m = pat.search(line)
            if m:
                desc = m.group("desc").strip()
                # skip obvious non-transaction lines
                if len(desc) < 2 or re.fullmatch(r"[\d,.\s]+", desc):
                    break
                rows.append({
                    "date": m.group("date"),
                    "description": desc,
                    "amount": m.group("amt").replace(",", ""),
                    "credit": bool(m.groupdict().get("cr")),
                })
                break
    return rows


# noise patterns: bank fees/tax/rewards/EMI-interest/payments — NOT real spends to split
_NOISE_RE = re.compile(
    r"(?i)\b(IGST|CGST|SGST|GST|cashback|cash back|reversal|finance charges?|late fee|"
    r"EMI,?\s?(INT|PRIN)|interest|tele transfer|payment received|autopay|"
    r"reward|surcharge|convenience fee|markup|annual fee|joining fee|goods & service tax)\b")


def clean_transactions(txns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep real purchase rows; drop credits and bank fee/tax/reward/EMI-interest noise.

    A statement's raw parse includes GST lines, cashback credits, finance charges, EMI interest,
    payments, and sometimes T&C/fee-schedule table rows with old dates. Those are NOT expenses to
    split — this filters them so only genuine merchant purchases remain for review/import.
    """
    out = []
    for t in txns:
        if t.get("credit"):
            continue
        desc = t.get("description", "")
        if _NOISE_RE.search(desc):
            continue
        # drop rows whose "date" year is far in the past (T&C/fee-schedule tables)
        yr = re.search(r"(20\d{2})", t.get("date", ""))
        if yr and int(yr.group(1)) < 2024:
            continue
        try:
            if float(t.get("amount", "0")) <= 0:
                continue
        except ValueError:
            continue
        out.append(t)
    return out


def unlock_and_parse(data: bytes, hints: Dict[str, Any]) -> Dict[str, Any]:
    """Full flow: derive passwords -> decrypt -> extract text -> parse transactions."""
    pwds = password_candidates(hints)
    decrypted = decrypt_pdf(data, pwds)
    text = extract_text(decrypted)
    txns = parse_transactions(text)
    return {
        "transaction_count": len(txns),
        "transactions": txns,
        "passwords_tried": len(pwds),
        "text_len": len(text),
    }
