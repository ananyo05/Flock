"""Parsers for Urja portal responses, formats, timestamps, and numbers."""

from datetime import date, datetime, timedelta, timezone
import json
import re
from typing import Any

# Asia/Kolkata timezone offset (+05:30)
# All portal timestamps are in local IST without explicit timezone offset.
IST = timezone(timedelta(hours=5, minutes=30))

HIERARCHY_PATTERN = re.compile(r"^(.*?)\s*(?:\(([^)]+)\))?$")


def parse_hierarchy_string(text: str | None) -> dict[str, str] | None:
    """Parse hierarchy string 'Name (CODE)' into {'name': ..., 'code': ...}.

    Handles strings without explicit codes gracefully by setting code equal to name.
    """
    if not text or not text.strip():
        return None

    cleaned = text.strip()
    match = HIERARCHY_PATTERN.match(cleaned)
    if not match:
        return {"name": cleaned, "code": cleaned}

    name = match.group(1).strip()
    code = match.group(2).strip() if match.group(2) else name
    return {"name": name, "code": code}


def parse_hierarchy(raw_hierarchy: dict[str, Any] | None) -> dict[str, Any]:
    """Parse raw hierarchy map into structured hierarchy levels."""
    if not raw_hierarchy:
        return {
            "zone": None,
            "circle": None,
            "division": None,
            "subdivision": None,
            "substation": None,
            "feeder": None,
            "dt": None,
        }

    # Handle Sub Station with space vs Substation without space
    substation_raw = raw_hierarchy.get("Sub Station") or raw_hierarchy.get("Substation")

    return {
        "zone": parse_hierarchy_string(raw_hierarchy.get("Zone")),
        "circle": parse_hierarchy_string(raw_hierarchy.get("Circle")),
        "division": parse_hierarchy_string(raw_hierarchy.get("Division")),
        "subdivision": parse_hierarchy_string(raw_hierarchy.get("Subdivision")),
        "substation": parse_hierarchy_string(substation_raw),
        "feeder": parse_hierarchy_string(raw_hierarchy.get("Feeder")),
        "dt": parse_hierarchy_string(raw_hierarchy.get("DT")),
    }


def parse_meter_detail(page_data: dict[str, Any]) -> dict[str, Any]:
    """Parse meter detail from unflattened SvelteKit page data.

    Supports both:
    Shape 1: {data: [{parameterName, parameterValue}, ...]}
    Shape 2: {classData: "<json string>"} with fields under installed_meter.
    """
    meter_id = page_data.get("meterId")
    detail = page_data.get("detail", {})

    fields: dict[str, Any] = {
        "meter_id": meter_id,
        "serial_no": None,
        "make": None,
        "phase_type": None,
        "installation_status": None,
        "installation_type": None,
    }

    if isinstance(detail, dict) and "data" in detail and isinstance(detail["data"], list):
        # Shape 1
        for item in detail["data"]:
            if not isinstance(item, dict):
                continue
            name = item.get("parameterName")
            val = item.get("parameterValue")
            if name == "Meter ID" and val:
                fields["meter_id"] = str(val).strip()
            elif name == "Serial No" and val:
                fields["serial_no"] = str(val).strip()
            elif name == "Make" and val:
                fields["make"] = str(val).strip()
            elif name == "Phase Type" and val:
                fields["phase_type"] = str(val).strip()
            elif name == "Installation Status" and val:
                fields["installation_status"] = str(val).strip()
            elif name == "Installation Type" and val:
                fields["installation_type"] = str(val).strip()

    elif isinstance(detail, dict) and "classData" in detail:
        # Shape 2: JSON encoded string
        class_data_str = detail["classData"]
        try:
            parsed = json.loads(class_data_str)
            installed = parsed.get("installed_meter", {})
            if installed.get("MeterId"):
                fields["meter_id"] = str(installed["MeterId"]).strip()
            if installed.get("SerialNo"):
                fields["serial_no"] = str(installed["SerialNo"]).strip()
            if installed.get("Make"):
                fields["make"] = str(installed["Make"]).strip()
            if installed.get("PhaseType"):
                fields["phase_type"] = str(installed["PhaseType"]).strip()
            if installed.get("InstallationStatus"):
                fields["installation_status"] = str(installed["InstallationStatus"]).strip()
            if installed.get("InstallationType"):
                fields["installation_type"] = str(installed["InstallationType"]).strip()
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback from hierarchy if detail was missing installation_type or status
    hierarchy = page_data.get("hierarchy", {})
    if isinstance(hierarchy, dict):
        if not fields.get("installation_status") and hierarchy.get("Installation Status"):
            fields["installation_status"] = str(hierarchy["Installation Status"]).strip()
        if not fields.get("installation_type") and hierarchy.get("Installation Type"):
            fields["installation_type"] = str(hierarchy["Installation Type"]).strip()
        if not fields.get("meter_id") and hierarchy.get("Meter ID"):
            fields["meter_id"] = str(hierarchy["Meter ID"]).strip()

    return fields


def parse_timestamp(ts_str: str) -> str:
    """Parse portal timestamp DD/MM/YYYY HH:mm into ISO-8601 with Asia/Kolkata offset.

    Returns: e.g. '2026-06-23T23:30:00+05:30'
    """
    if not ts_str or not ts_str.strip():
        raise ValueError("Timestamp is empty")

    dt = datetime.strptime(ts_str.strip(), "%d/%m/%Y %H:%M")
    dt_with_tz = dt.replace(tzinfo=IST)
    return dt_with_tz.isoformat()


def parse_float(val: Any) -> float | None:
    """Convert energy string to float, mapping blank, dash or null to None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ("—", "-", "null", "None"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def validate_date_string(date_str: str, param_name: str) -> str:
    """Validate that date string is strictly in YYYY-MM-DD format."""
    if not date_str or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        raise ValueError(
            f"Invalid {param_name} date format '{date_str}'. Expected format is YYYY-MM-DD."
        )
    try:
        # Validate calendar validity (e.g. leap year, month ranges)
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(
            f"Invalid {param_name} date '{date_str}': {str(e)}"
        ) from e
    return date_str


def validate_date_range(from_date: str | None, to_date: str | None) -> tuple[str | None, str | None]:
    """Validate query date parameters 'from' and 'to'.

    Ensures both conform to YYYY-MM-DD and from_date <= to_date.
    """
    valid_from = validate_date_string(from_date, "from") if from_date else None
    valid_to = validate_date_string(to_date, "to") if to_date else None

    if valid_from and valid_to:
        if valid_from > valid_to:
            raise ValueError(
                f"Query parameter 'from' ({valid_from}) must be less than or equal to 'to' ({valid_to})."
            )

    return valid_from, valid_to


def process_energy_readings(raw_readings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Process raw portal energy readings.

    Converts timestamps to ISO-8601 with Asia/Kolkata (+05:30) offset,
    converts register values to floats, computes consumption_kwh deltas,
    flags negative deltas as anomalies, and infers reading interval.
    """
    if not raw_readings:
        return [], "daily"

    parsed_entries: list[dict[str, Any]] = []

    for item in raw_readings:
        ts_raw = item.get("timestamp")
        if not ts_raw:
            continue
        try:
            iso_ts = parse_timestamp(ts_raw)
            dt_obj = datetime.fromisoformat(iso_ts)
        except ValueError:
            continue

        kwh = parse_float(item.get("kwh"))
        kvah = parse_float(item.get("kvah"))
        volt_r = parse_float(item.get("voltR"))

        parsed_entries.append({
            "_dt": dt_obj,
            "timestamp": iso_ts,
            "kwh": kwh,
            "kvah": kvah,
            "volt_r": volt_r,
            "consumption_kwh": None,
            "anomaly": False,
        })

    # Sort readings chronologically
    parsed_entries.sort(key=lambda x: x["_dt"])

    # Infer interval from consecutive timestamps
    time_diffs_sec: list[float] = []
    for i in range(1, len(parsed_entries)):
        diff = (parsed_entries[i]["_dt"] - parsed_entries[i - 1]["_dt"]).total_seconds()
        if diff > 0:
            time_diffs_sec.append(diff)

    interval = "daily"
    if time_diffs_sec:
        median_diff = sorted(time_diffs_sec)[len(time_diffs_sec) // 2]
        if median_diff <= 3600:  # ~1800s (30 mins)
            interval = "half_hourly"
        else:
            interval = "daily"

    # Compute deltas and anomaly flags
    prev_kwh: float | None = None
    for entry in parsed_entries:
        curr_kwh = entry["kwh"]
        if prev_kwh is not None and curr_kwh is not None:
            delta = round(curr_kwh - prev_kwh, 4)
            entry["consumption_kwh"] = delta
            if delta < 0:
                entry["anomaly"] = True
            else:
                entry["anomaly"] = False
        else:
            entry["consumption_kwh"] = None
            entry["anomaly"] = False

        if curr_kwh is not None:
            prev_kwh = curr_kwh

        # Clean internal helper
        del entry["_dt"]

    return parsed_entries, interval
