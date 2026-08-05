"""Tests for password_rule — parsing bank-stated password rules and deriving passwords.

Anchors each parser to the bank's OWN worked example (e.g. SBI's doc says mobile XXXXX12345 +
16 Sep 1982 -> 12345160982), which is the strongest correctness check we can write without a
real encrypted PDF.
"""

from splitwise_mcp_server import password_rule as pr


SBI_RULE = (
    "Your e-account statement is protected by a password, which is the last five digits of "
    "customer registered mobile number and date of birth (DOB) in DDMMYY format registered with "
    "Bank, for example if mobile number is XXXXX12345 and DOB is 16th September 1982 then password "
    "will be 12345160982."
)
RBL_RULE = (
    "Password is the first four letters of your name in CAPITALS followed by your date of birth "
    "in DDMMYY format. For example ANSH140919"
)
HDFC_RULE = "first 4 characters of your name in lower case and date of birth in DDMM format"


def test_sbi_rule_components_and_order():
    comps = pr.parse_password_rule(SBI_RULE)
    assert comps == [{"kind": "mobile", "take": 5}, {"kind": "dob", "fmt": "ddmmyy"}]


def test_sbi_rule_matches_banks_own_example():
    # SBI's documented example: mobile ...12345, DOB 16-09-1982 -> 12345160982
    pwds = pr.passwords_from_rule_text(SBI_RULE, {"mobile": "0000012345", "dob": "16091982"})
    assert pwds == ["12345160982"]


def test_sbi_rule_multi_component_order():
    # synthetic hints (never real user data): mobile-last5 (54321) + DDMMYY (010190)
    pwds = pr.passwords_from_rule_text(SBI_RULE, {"mobile": "9990054321", "dob": "01011990"})
    assert pwds == ["54321010190"]


def test_rbl_rule_caps_name_then_ddmmyy():
    comps = pr.parse_password_rule(RBL_RULE)
    assert comps == [{"kind": "name", "take": 4, "case": "upper"}, {"kind": "dob", "fmt": "ddmmyy"}]
    pwds = pr.passwords_from_rule_text(RBL_RULE, {"name": "Sample Name", "dob": "01011990"})
    assert pwds == ["SAMP010190"]


def test_hdfc_rule_lowercase_name_then_ddmm():
    pwds = pr.passwords_from_rule_text(HDFC_RULE, {"name": "Sample", "dob": "01011990"})
    assert pwds == ["samp0101"]


def test_unknown_case_yields_variants_upper_first():
    rule = "first four letters of your name and date of birth in DDMMYY"
    pwds = pr.passwords_from_rule_text(rule, {"name": "Sample", "dob": "01011990"})
    assert pwds[0] == "SAMP010190"                 # upper tried first (most banks)
    assert set(pwds) == {"SAMP010190", "samp010190", "Samp010190"}


def test_missing_facts_returns_empty_so_caller_falls_back():
    # rule needs mobile but none provided -> [] (caller uses brute-force candidates instead)
    assert pr.passwords_from_rule_text(SBI_RULE, {"dob": "12112000"}) == []


def test_no_rule_recognized_returns_empty():
    assert pr.parse_password_rule("Please find your statement attached.") == []
    assert pr.parse_password_rule("") == []


def test_ocr_image_actionable_error_without_deps():
    # Without pytesseract/tesseract, ocr_image must raise a clear, actionable error (not crash).
    try:
        import pytesseract  # noqa
        import PIL  # noqa
    except ImportError:
        import pytest
        with pytest.raises(RuntimeError, match="agent vision"):
            pr.ocr_image(b"not-an-image")
