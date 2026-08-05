"""PDF statement tests — password derivation + transaction parsing (pure, no real PDF)."""

import pytest
from splitwise_mcp_server import pdf_statement as p


def test_hdfc_password_candidates():
    pwds = p.password_candidates({"name": "Uday Srinu", "dob": "07-03-1999", "card_last4": "8285"})
    assert "uday0703" in pwds          # HDFC: first4 lower + DDMM
    assert "UDAY0703" in pwds
    assert "82850703" in pwds          # card4 + DDMM (SBI/OneCard style)
    assert pwds[0]                     # non-empty ordering


def test_custom_passwords_first():
    pwds = p.password_candidates({"custom": ["mysecret"], "name": "Uday", "dob": "0703"})
    assert pwds[0] == "mysecret"


def test_password_candidates_dedup_and_empty_hints():
    assert p.password_candidates({}) == []
    pwds = p.password_candidates({"name": "Uday", "dob": "07031999"})
    assert len(pwds) == len(set(pwds))   # no dupes


def test_parse_transactions_ddmmyyyy_trailing_amount():
    text = "\n".join([
        "Statement of transactions",
        "11/07/2026  RAZ*SWIGGY BANGALORE           1,508.00",
        "24/07/2026  SWIGGY INSTA MART GR            550.00",
        "15/07/2026  PAYMENT RECEIVED             5,000.00 Cr",
        "Total   2,058.00",
    ])
    rows = p.parse_transactions(text)
    swiggy = [r for r in rows if "SWIGGY" in r["description"]]
    assert len(swiggy) >= 2
    r0 = next(r for r in rows if r["amount"] == "1508.00")
    assert r0["date"] == "11/07/2026" and r0["credit"] is False
    cr = next(r for r in rows if r["amount"] == "5000.00")
    assert cr["credit"] is True        # payment/refund flagged


def test_parse_transactions_dd_mon_yyyy():
    text = "11 Jul 2026  KFC BANGALORE  497.00"
    rows = p.parse_transactions(text)
    assert rows and rows[0]["amount"] == "497.00" and "KFC" in rows[0]["description"]


def test_parse_skips_pure_number_lines():
    text = "1,234.00\n11/07/2026  REAL MERCHANT  100.00"
    rows = p.parse_transactions(text)
    assert len(rows) == 1 and rows[0]["description"] == "REAL MERCHANT"


def test_clean_transactions_drops_noise_and_credits():
    txns = [
        {"date": "16/06/2026", "description": "USHA NARAYANA REDDYS", "amount": "4047.20", "credit": False},
        {"date": "01/07/2026", "description": "FINANCE CHARGES (Ref# 199...)", "amount": "888.28", "credit": False},
        {"date": "01/07/2026", "description": "IGST-VPS...", "amount": "160.00", "credit": False},
        {"date": "15/06/2026", "description": "PAYMENT RECEIVED", "amount": "5000.00", "credit": True},
        {"date": "10/06/2026", "description": "Amazon cashback", "amount": "50.00", "credit": False},
        {"date": "12/2019", "description": "FEE SCHEDULE ROW", "amount": "500.00", "credit": False},
    ]
    kept = p.clean_transactions(txns)
    descs = [t["description"] for t in kept]
    assert descs == ["USHA NARAYANA REDDYS"]          # only the real purchase survives
    assert not any("FINANCE CHARGES" in d for d in descs)   # plural 's' regression (was leaking)


def test_password_candidates_mobile_and_rule_text():
    # SBI-style: mobile-last5 + DDMMYY, via brute-force fallback (synthetic hints, not real data)
    pwds = p.password_candidates({"mobile": "9990054321", "dob": "01011990"})
    assert "54321010190" in pwds
    # rule_text (bank-stated) is derived and placed FIRST
    rule = "last five digits of registered mobile number and date of birth in DDMMYY format"
    pwds2 = p.password_candidates({"mobile": "9990054321", "dob": "01011990", "rule_text": rule})
    assert pwds2[0] == "54321010190"


def test_decrypt_pdf_missing_lib_or_unencrypted(monkeypatch):
    # a minimal unencrypted PDF should return unchanged (if pikepdf present);
    # if pikepdf missing, raises PdfDecryptError — either way, no crash on our code path.
    minimal = b"%PDF-1.4\n%%EOF\n"
    try:
        import pikepdf  # noqa
    except ImportError:
        with pytest.raises(p.PdfDecryptError):
            p.decrypt_pdf(minimal, ["x"])
        return
    # pikepdf present: minimal isn't a valid PDF, so it should raise PasswordError path -> our error
    with pytest.raises(Exception):
        p.decrypt_pdf(minimal, ["x"])
