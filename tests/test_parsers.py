from datetime import date
import json
from pathlib import Path
import pytest

from app.portal.devalue import decode_sveltekit_page_data
from app.portal.parsers import (
    parse_float,
    parse_hierarchy,
    parse_hierarchy_string,
    parse_meter_detail,
    parse_timestamp,
    process_energy_readings,
    validate_date_range,
    validate_date_string,
)


def test_parse_hierarchy_string():
    res1 = parse_hierarchy_string("Jaipur Zone 1 (Z-01)")
    assert res1 == {"name": "Jaipur Zone 1", "code": "Z-01"}

    res2 = parse_hierarchy_string("Civil Lines DT 40 (DT-040)")
    assert res2 == {"name": "Civil Lines DT 40", "code": "DT-040"}

    # String without explicit code
    res3 = parse_hierarchy_string("Circle 5")
    assert res3 == {"name": "Circle 5", "code": "Circle 5"}

    # None or empty
    assert parse_hierarchy_string(None) is None
    assert parse_hierarchy_string("  ") is None


def test_parse_hierarchy_full_and_missing_levels():
    raw = {
        "Zone": "Jaipur Zone 1 (Z-01)",
        "Circle": "Circle 1 (C-01)",
        "Division": "Division 1 (D-01)",
        "Subdivision": "Subdivision 1 (SD-01)",
        "Sub Station": "Substation 1 (SS-01)",
        "Feeder": "Feeder 1 (F-001)",
        "DT": "Malviya Nagar DT 1 (DT-001)",
    }
    hier = parse_hierarchy(raw)
    assert hier["zone"]["code"] == "Z-01"
    assert hier["substation"]["code"] == "SS-01"
    assert hier["dt"]["code"] == "DT-001"

    # Missing levels tolerated
    partial = {"Zone": "Jaipur Zone 1 (Z-01)"}
    partial_hier = parse_hierarchy(partial)
    assert partial_hier["zone"]["code"] == "Z-01"
    assert partial_hier["circle"] is None
    assert partial_hier["dt"] is None


def test_parse_meter_detail_shape_1(detail_samples_dir: Path):
    with open(detail_samples_dir / "J100000.json") as f:
        data = json.load(f)
    page_data = decode_sveltekit_page_data(data)
    detail = parse_meter_detail(page_data)

    assert detail["meter_id"] == "J100000"
    assert detail["serial_no"] == "SE33962"
    assert detail["make"] == "HPL"
    assert detail["phase_type"] == "single"
    assert detail["installation_status"] == "Decommissioned"
    assert detail["installation_type"] == "Whole Current"


def test_parse_meter_detail_shape_2(detail_samples_dir: Path):
    with open(detail_samples_dir / "J100004.json") as f:
        data = json.load(f)
    page_data = decode_sveltekit_page_data(data)
    detail = parse_meter_detail(page_data)

    assert detail["meter_id"] == "J100004"
    assert detail["serial_no"] == "SE65293"
    assert detail["make"] == "Genus"
    assert detail["phase_type"] == "single"
    assert detail["installation_status"] == "Faulty"
    assert detail["installation_type"] == "CT Operated"


def test_parse_timestamp():
    res = parse_timestamp("23/06/2026 23:30")
    assert res == "2026-06-23T23:30:00+05:30"

    with pytest.raises(ValueError):
        parse_timestamp("invalid-date")


def test_parse_float():
    assert parse_float("37562.9") == 37562.9
    assert parse_float("0") == 0.0
    assert parse_float("") is None
    assert parse_float("—") is None
    assert parse_float("-") is None
    assert parse_float(None) is None


def test_validate_date_string_and_range():
    assert validate_date_string("2026-06-24", "from") == "2026-06-24"

    with pytest.raises(ValueError, match="Invalid from date format"):
        validate_date_string("24/06/2026", "from")

    with pytest.raises(ValueError, match="Invalid to date format"):
        validate_date_string("2026-06-24T00:00:00Z", "to")

    valid_from, valid_to = validate_date_range("2026-06-20", "2026-06-25")
    assert valid_from == "2026-06-20"
    assert valid_to == "2026-06-25"

    with pytest.raises(ValueError, match="must be less than or equal"):
        validate_date_range("2026-06-25", "2026-06-20")


def test_process_energy_readings(samples_dir: Path):
    with open(samples_dir / "energy_sample.json") as f:
        energy_data = json.load(f)

    readings, interval = process_energy_readings(energy_data["data"])
    assert interval == "half_hourly"
    assert len(readings) == len(energy_data["data"])

    # First reading has no delta
    assert readings[0]["consumption_kwh"] is None
    assert readings[0]["anomaly"] is False
    assert readings[0]["timestamp"] == "2026-06-23T23:30:00+05:30"

    # Second reading has delta
    assert readings[1]["consumption_kwh"] == pytest.approx(0.42, 0.01)
    assert readings[1]["anomaly"] is False


def test_process_energy_readings_with_anomaly_and_daily():
    raw = [
        {"timestamp": "24/06/2026 00:00", "kwh": "100.0", "kvah": "110.0", "voltR": "230"},
        {"timestamp": "25/06/2026 00:00", "kwh": "95.0", "kvah": "105.0", "voltR": "230"},  # drop -> negative delta
        {"timestamp": "26/06/2026 00:00", "kwh": "102.0", "kvah": "112.0", "voltR": "230"},
    ]
    readings, interval = process_energy_readings(raw)
    assert interval == "daily"
    assert len(readings) == 3

    assert readings[0]["consumption_kwh"] is None
    assert readings[0]["anomaly"] is False

    assert readings[1]["consumption_kwh"] == -5.0
    assert readings[1]["anomaly"] is True

    assert readings[2]["consumption_kwh"] == 7.0
    assert readings[2]["anomaly"] is False
