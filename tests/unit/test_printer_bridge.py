import importlib.util
import sys
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[2] / "printer-bridge"
sys.path.insert(0, str(BRIDGE_DIR))
SPEC = importlib.util.spec_from_file_location("larder_printer_bridge", BRIDGE_DIR / "bridge.py")
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


def job(**overrides):
    values = {
        "job_id": "00000000-0000-0000-0000-000000000001",
        "kind": "product",
        "value": "0096619120291",
        "title": "Colombian Supremo Whole Bean Coffee",
        "subtitle": "Product Barcode",
        "copies": 1,
        "attempt": 1,
    }
    values.update(overrides)
    return bridge.PrintJob(**values)


def test_render_tspl_uses_driver_compatible_bitmap():
    payload = bridge.render_tspl(job())
    assert payload.startswith(b"SIZE 57 mm,31 mm\r\nGAP 3 mm,0 mm")
    marker = b"BITMAP 0,0,57,254,1,"
    assert marker in payload
    raster_start = payload.index(marker) + len(marker)
    assert payload[raster_start] == 0xFF
    raster = payload[raster_start : raster_start + (57 * 254)]
    assert raster[-57:] == b"\xff" * 57
    assert payload.endswith(b"\r\nPRINT 1,1\r\n")
    assert len(payload) > 14_000


def test_render_tspl_rasterizes_text_and_rejects_control_characters():
    payload = bridge.render_tspl(job(title='Cafe "Special"'))
    assert b"BITMAP 0,0,57,254,1," in payload
    try:
        bridge.render_tspl(job(value="CMD:MODE:ADD\nPRINT 99"))
    except ValueError as error:
        assert "printable ASCII" in str(error)
    else:
        raise AssertionError("control characters must be rejected")


def test_render_tspl_supports_store_control_barcodes():
    for value in ("CMD:STORE:COSTCO", "CMD:STORE:WALMART", "CMD:STORE:WEGMANS"):
        payload = bridge.render_tspl(job(value=value, title=value.removeprefix("CMD:STORE:")))
        assert b"BITMAP 0,0,57,254,1," in payload
        assert payload.endswith(b"\r\nPRINT 1,1\r\n")


def test_store_barcode_uses_available_width_with_quiet_zones():
    image = bridge.render_code128("CMD:STORE:COSTCO", 448, 176)
    black_x = [x for x in range(image.width) if image.getpixel((x, 20)) == 0]
    assert image.width == 448
    assert min(black_x) < 30
    assert max(black_x) > 418


def test_digest_is_stable_across_delivery_attempts():
    assert job(attempt=1).digest() == job(attempt=2).digest()
