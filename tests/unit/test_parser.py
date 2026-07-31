import unittest

from larder.domain.enums import ScanKind
from larder.scanner.parser import (
    BarcodeError,
    FrameError,
    audit_text,
    has_valid_gtin_check_digit,
    normalize_gtin,
    parse_frame,
)
from larder.scanner.supervisor import bind_scanner_frame


class FrameParserTests(unittest.TestCase):
    def test_binds_plain_scanner_payload_to_configured_identity(self):
        self.assertEqual(
            bind_scanner_frame("SCN01", b"4006381333931\r\n"),
            b"LDR:SCN01|4006381333931\r\n",
        )

    def test_does_not_double_wrap_protocol_frame(self):
        frame = b"LDR:SCN01|CMD:MODE:ADD\n"
        self.assertEqual(bind_scanner_frame("SCN01", frame), frame)

    def test_parses_product_and_normalizes_crlf(self):
        parsed = parse_frame(b"LDR:SCN01|4006381333931\r\n")
        self.assertEqual(parsed.scanner_code, "SCN01")
        self.assertEqual(parsed.payload, "4006381333931")
        self.assertEqual(parsed.raw_frame, "LDR:SCN01|4006381333931")
        self.assertEqual(parsed.kind, ScanKind.PRODUCT)

    def test_strips_redundant_hardware_scanner_prefix_but_preserves_raw_frame(self):
        parsed = parse_frame(b"LDR:SCN01|SCN01613008725914\n")
        self.assertEqual(parsed.scanner_code, "SCN01")
        self.assertEqual(parsed.payload, "613008725914")
        self.assertEqual(parsed.raw_frame, "LDR:SCN01|SCN01613008725914")
        self.assertEqual(parsed.kind, ScanKind.PRODUCT)

    def test_strips_redundant_hardware_prefix_from_control_barcode(self):
        parsed = parse_frame(b"LDR:SCN01|SCN01CMD:MODE:ADD\n")
        self.assertEqual(parsed.payload, "CMD:MODE:ADD")
        self.assertEqual(parsed.kind, ScanKind.CONTROL)

    def test_classifies_control_without_gtin_validation(self):
        parsed = parse_frame(b"LDR:SCN01|CMD:MODE:ADD\n")
        self.assertEqual(parsed.kind, ScanKind.CONTROL)

    def test_accepts_underscore_control_separators_and_preserves_raw_frame(self):
        parsed = parse_frame(b"LDR:SCN01|CMD_MODE_ADD\n")
        self.assertEqual(parsed.payload, "CMD:MODE:ADD")
        self.assertEqual(parsed.raw_frame, "LDR:SCN01|CMD_MODE_ADD")
        self.assertEqual(parsed.kind, ScanKind.CONTROL)

    def test_accepts_mixed_control_separators(self):
        parsed = parse_frame(b"LDR:SCN01|CMD_STORE:CLEAR\n")
        self.assertEqual(parsed.payload, "CMD:STORE:CLEAR")
        self.assertEqual(parsed.kind, ScanKind.CONTROL)

    def test_rejects_wrong_scanner_for_port(self):
        with self.assertRaises(FrameError) as raised:
            parse_frame(b"LDR:SCN02|4006381333931\n", expected_scanner_code="SCN01")
        self.assertEqual(raised.exception.code, "UNEXPECTED_SCANNER_CODE")

    def test_rejects_non_utf8_and_preserves_audit_representation(self):
        with self.assertRaises(FrameError) as raised:
            parse_frame(b"LDR:SCN01|\xff\n")
        self.assertEqual(raised.exception.code, "NON_UTF8_FRAME")
        self.assertIn("\\xff", raised.exception.raw_frame)

    def test_rejects_oversized_and_unterminated_frames(self):
        with self.assertRaises(FrameError) as oversized:
            parse_frame(b"LDR:SCN01|1234567890\n", max_frame_bytes=10)
        self.assertEqual(oversized.exception.code, "FRAME_TOO_LONG")
        with self.assertRaises(FrameError) as unterminated:
            parse_frame(b"LDR:SCN01|4006381333931")
        self.assertEqual(unterminated.exception.code, "UNTERMINATED_FRAME")

    def test_oversized_audit_text_is_database_bounded(self):
        self.assertEqual(len(audit_text(b"x" * 5000)), 4096)


class GtinTests(unittest.TestCase):
    def test_known_valid_check_digits(self):
        for value in ("96385074", "036000291452", "4006381333931", "10012345000017"):
            with self.subTest(value=value):
                self.assertTrue(has_valid_gtin_check_digit(value))

    def test_upca_keeps_leading_zero_and_normalizes_to_ean_compatible_key(self):
        result = normalize_gtin("036000291452")
        self.assertEqual(result.raw, "036000291452")
        self.assertEqual(result.barcode, "0036000291452")
        self.assertEqual(result.barcode_format, "UPC-A")
        self.assertEqual(result.normalization_version, 1)

    def test_arnold_palmer_upca_normalizes_for_open_food_facts(self):
        result = normalize_gtin("613008725914")
        self.assertEqual(result.barcode, "0613008725914")
        self.assertEqual(result.barcode_format, "UPC-A")

    def test_invalid_check_digit_is_rejected_by_default(self):
        with self.assertRaises(BarcodeError) as raised:
            normalize_gtin("4006381333932")
        self.assertEqual(raised.exception.code, "GTIN_BAD_CHECK_DIGIT")

    def test_invalid_check_digit_can_be_explicitly_accepted(self):
        result = normalize_gtin("4006381333932", accept_invalid_check_digit=True)
        self.assertEqual(result.barcode, "4006381333932")

    def test_non_ascii_decimal_digits_are_rejected(self):
        with self.assertRaises(BarcodeError):
            normalize_gtin("４００６３８１３３３９３１")


if __name__ == "__main__":
    unittest.main()
