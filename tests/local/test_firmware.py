from pathlib import Path

from vsd.local.firmware import FirmwareAnalyzer


def test_raw_firmware_and_compare(tmp_path):
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"Hello firmware\x00" + bytes(range(64)))
    second.write_bytes(b"Hello firmware\x00" + bytes(range(63)) + b"X")
    analyzer = FirmwareAnalyzer()
    report = analyzer.analyze(first, "arm")
    assert report["format"] == "Raw binary"
    assert report["size"] == first.stat().st_size
    assert any("Hello firmware" in item["value"] for item in report["strings"])
    comparison = analyzer.compare(first, second)
    assert comparison["changed_bytes"] >= 1


def test_intel_hex_detection(tmp_path):
    image = tmp_path / "test.hex"
    image.write_text(":0400000001020304F2\n:00000001FF\n")
    report = FirmwareAnalyzer().analyze(image)
    assert report["format"] == "Intel HEX"
    assert report["intel_hex"]["data_bytes"] == 4
