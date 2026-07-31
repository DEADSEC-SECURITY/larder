import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "scanner-logger"))

from payloads import strip_scanner_prefix  # noqa: E402


def test_strips_hardware_scanner_prefix_from_product_and_control_payloads():
    assert strip_scanner_prefix("SCN01", "SCN01613008725914") == "613008725914"
    assert strip_scanner_prefix("SCN01", "SCN01CMD:MODE:ADD") == "CMD:MODE:ADD"


def test_does_not_turn_scanner_code_only_into_an_empty_payload():
    assert strip_scanner_prefix("SCN01", "SCN01") == "SCN01"
    assert strip_scanner_prefix("SCN01", "613008725914") == "613008725914"
