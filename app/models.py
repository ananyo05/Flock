"""Pydantic v2 data models for the Urja API."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Structured error payload."""

    code: str = Field(..., description="Machine-readable error code", examples=["NOT_FOUND"])
    message: str = Field(..., description="Human-readable error explanation", examples=["Meter J999999 not found"])


class ErrorResponse(BaseModel):
    """Standard error response model."""

    error: ErrorDetail


class Location(BaseModel):
    """Geographic coordinates."""

    latitude: float | None = Field(None, description="Latitude in decimal degrees", examples=[26.98325])
    longitude: float | None = Field(None, description="Longitude in decimal degrees", examples=[75.88163])


class HierarchyItem(BaseModel):
    """Single level in electrical distribution hierarchy."""

    name: str = Field(..., description="Entity display name", examples=["Jaipur Zone 1"])
    code: str = Field(..., description="Entity standard identifier code", examples=["Z-01"])


class StructuredHierarchy(BaseModel):
    """Complete distribution hierarchy for a meter."""

    zone: HierarchyItem | None = Field(None, description="Zone level")
    circle: HierarchyItem | None = Field(None, description="Circle level")
    division: HierarchyItem | None = Field(None, description="Division level")
    subdivision: HierarchyItem | None = Field(None, description="Subdivision level")
    substation: HierarchyItem | None = Field(None, description="Substation level")
    feeder: HierarchyItem | None = Field(None, description="Feeder level")
    dt: HierarchyItem | None = Field(None, description="Distribution Transformer level")


class MeterSummary(BaseModel):
    """Normalized meter summary representation for search and listing."""

    meter_id: str = Field(..., description="Unique meter ID", examples=["J100004"])
    serial_no: str = Field(..., description="Manufacturer serial number", examples=["SE65293"])
    make: str = Field(..., description="Meter manufacturer", examples=["Genus"])
    phase_type: str = Field(..., description="Phase configuration (single or three)", examples=["single"])
    installation_status: str = Field(..., description="Status (Installed, Faulty, Decommissioned)", examples=["Faulty"])
    dt_code: str = Field(..., description="Distribution transformer code", examples=["DT-005"])
    installation_type: str | None = Field(None, description="Installation type (Whole Current, CT Operated)", examples=["CT Operated"])


class PaginatedMetersResponse(BaseModel):
    """Paginated response envelope for meter listings."""

    items: list[MeterSummary] = Field(..., description="List of meters on the current page")
    total: int = Field(..., description="Total count of meters matching query filters", examples=[403])
    page: int = Field(..., description="Current 1-based page number", examples=[1])
    page_size: int = Field(..., description="Number of items per page", examples=[20])


class MeterDetailResponse(BaseModel):
    """Detailed meter information including coordinates and hierarchy."""

    meter_id: str = Field(..., description="Unique meter ID", examples=["J100000"])
    serial_no: str = Field(..., description="Manufacturer serial number", examples=["SE33962"])
    make: str = Field(..., description="Meter manufacturer", examples=["HPL"])
    phase_type: str = Field(..., description="Phase configuration", examples=["single"])
    installation_status: str = Field(..., description="Operating status", examples=["Decommissioned"])
    installation_type: str | None = Field(None, description="Installation type", examples=["Whole Current"])
    location: Location | None = Field(None, description="Meter coordinates")
    hierarchy: StructuredHierarchy = Field(..., description="Structured distribution tree levels")


class EnergyReading(BaseModel):
    """Single energy interval reading with consumption delta and anomaly detection."""

    timestamp: str = Field(..., description="ISO-8601 timestamp with Asia/Kolkata offset (+05:30)", examples=["2026-06-23T23:30:00+05:30"])
    kwh: float | None = Field(None, description="Cumulative active energy register in kWh", examples=[48438.74])
    kvah: float | None = Field(None, description="Cumulative apparent energy register in kVAh", examples=[52313.84])
    volt_r: float | None = Field(None, description="R-phase voltage in volts", examples=[226.0])
    consumption_kwh: float | None = Field(None, description="Calculated incremental consumption for interval", examples=[0.42])
    anomaly: bool = Field(False, description="Flagged true if register decreased (negative consumption delta)")


class ConsumptionResponse(BaseModel):
    """Meter energy consumption time series response."""

    meter_id: str = Field(..., description="Unique meter ID", examples=["J100000"])
    interval: Literal["half_hourly", "daily"] = Field(..., description="Inferred data resolution", examples=["half_hourly"])
    from_date: str | None = Field(None, description="Start date filter (YYYY-MM-DD)", examples=["2026-06-24"])
    to_date: str | None = Field(None, description="End date filter (YYYY-MM-DD)", examples=["2026-06-26"])
    readings: list[EnergyReading] = Field(..., description="Chronological energy readings")


class TransformerSummary(BaseModel):
    """Distribution transformer details and meter statistics."""

    code: str = Field(..., description="Distribution transformer code", examples=["DT-007"])
    name: str = Field(..., description="Transformer name", examples=["Old Malviya Nagar Xfmr"])
    feeder_code: str | None = Field(None, description="Associated feeder code", examples=["F-007"])
    capacity_kva: str | float | None = Field(None, description="Capacity in kVA", examples=["250"])
    meter_count: int = Field(..., description="Number of meters connected to this DT", examples=[13])


class TransformersListResponse(BaseModel):
    """Envelope for transformer listing."""

    items: list[TransformerSummary] = Field(..., description="List of distribution transformers")
    total: int = Field(..., description="Total transformer count", examples=[40])


class DataQualityIssue(BaseModel):
    """Inconsistency identified in portal data."""

    meter_id: str = Field(..., description="Meter exhibiting inconsistency", examples=["J100044"])
    issue_type: str = Field(..., description="Category of anomaly", examples=["feeder_mismatch"])
    description: str = Field(..., description="Detailed description of inconsistency", examples=["DT DT-005 assigned to feeder Feeder 5, previously observed under F-005"])


class HierarchyTreeNode(BaseModel):
    """Recursive node in distribution hierarchy tree."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(..., description="Node name", examples=["Jaipur Zone 1"])
    code: str = Field(..., description="Node code", examples=["Z-01"])
    level: str = Field(..., description="Hierarchy depth: zone, circle, division, subdivision, substation, feeder, dt", examples=["zone"])
    meter_count: int = Field(0, description="Total meters installed under this node branch", examples=[120])
    children: list["HierarchyTreeNode"] = Field(default_factory=list, description="Child nodes in distribution tree")


class HierarchyTreeResponse(BaseModel):
    """Full distribution hierarchy tree and data quality report."""

    total_meters: int = Field(..., description="Total meters observed in hierarchy", examples=[403])
    tree: list[HierarchyTreeNode] = Field(..., description="Top-level zone nodes")
    data_quality: list[DataQualityIssue] = Field(..., description="Observed structural discrepancies across meters")


class NearbyMeter(BaseModel):
    """Meter with distance relative to query point."""

    meter_id: str = Field(..., description="Unique meter ID", examples=["J100000"])
    serial_no: str = Field(..., description="Serial number", examples=["SE33962"])
    make: str = Field(..., description="Make", examples=["HPL"])
    dt_code: str = Field(..., description="Distribution transformer code", examples=["DT-001"])
    location: Location = Field(..., description="Meter geographic coordinates")
    distance_km: float = Field(..., description="Calculated distance in kilometers via Haversine formula", examples=[1.25])


class NearbyMetersResponse(BaseModel):
    """Nearby meters spatial search response."""

    center_lat: float = Field(..., description="Center latitude", examples=[26.983])
    center_lon: float = Field(..., description="Center longitude", examples=[75.881])
    radius_km: float = Field(..., description="Search radius in kilometers", examples=[5.0])
    total: int = Field(..., description="Number of meters found within radius", examples=[15])
    items: list[NearbyMeter] = Field(..., description="Meters sorted by proximity")


class RefreshResponse(BaseModel):
    """Admin cache reload response."""

    status: str = Field(..., description="Status message", examples=["Cache successfully invalidated and reloaded"])
    meters_count: int = Field(..., description="Count of meters loaded into cache", examples=[403])
    dts_count: int = Field(..., description="Count of distribution transformers loaded", examples=[40])
    cleared_cache_entries: int = Field(..., description="Count of previously cached entries evicted", examples=[12])
