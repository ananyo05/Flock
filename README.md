# Urja Meter Ops REST API

A clean, documented, and resilient REST API service wrapper in front of the legacy **Urja Meter Ops** web portal.

## Overview

The Urja legacy portal is a read-only SvelteKit web application managing smart electricity meters and distribution transformers across electrical utilities in Jaipur, Rajasthan. It exposes no official public API and relies on private JSON endpoints, SvelteKit index-based `devalue` page data serialization, and an HMAC-SHA256 signed export route.

This service acts as a modern, documented facade providing:
- **Clean REST Endpoints** with snake_case fields and paginated envelopes.
- **Robust Authentication & Session Management**: Cookie jar storage, proactive session re-login before 1-hour expiry, reactive re-login on session failure with concurrency locks, and exponential backoff retries.
- **SvelteKit Devalue Decoding**: Custom decoder resolving flat index-based graphs, negative sentinel codes (`undefined`, `NaN`, `±Infinity`, `-0`), and cyclic references.
- **Polite Polling & Concurrency Limits**: Outgoing requests bounded to max 4 concurrent requests with polite throttling delays.
- **Detail Normalization**: Dual-shape parsing (handling both parameter name/value lists and nested `classData` JSON strings).
- **Time-Series Registers & Anomaly Detection**: Strict date validation (`YYYY-MM-DD`), ISO-8601 timestamps with Asia/Kolkata offset (`+05:30`), delta calculation (`consumption_kwh`), anomaly flagging for negative deltas, and automated reading interval inference (`half_hourly` vs `daily`).
- **Distribution Hierarchy & Data Quality Auditing**: Reconstructing the Zone > Circle > Division > Subdivision > Substation > Feeder > DT tree from observed parent-child pairs while surfacing data inconsistencies.
- **Spatial Search (Optional Extension)**: `/meters/nearby` endpoint utilizing bounding-box prefiltering and the Haversine distance formula.
- **In-Memory TTL Caching**: Metadata cache (default 15 minutes) and consumption cache (default 60s), cache invalidation via `POST /admin/refresh`, and `X-Cache-Age` response headers.
- **Interactive Documentation**: OpenAPI 3.1 specification at `/openapi.json` and interactive Swagger UI at `/docs`.

Reference: For legacy protocol analysis and reverse-engineering findings, refer to [PROTOCOL.md](file:///Users/srabaniadhikary/Desktop/urja-api/PROTOCOL.md).

---

## Project Layout

```
urja-api/
├── app/
│   ├── main.py              # FastAPI application, lifespan, routes, error handlers
│   ├── config.py            # Pydantic BaseSettings from .env
│   ├── dependencies.py      # Dependency injection providers
│   ├── cache.py             # In-memory TTL cache with entry age tracking
│   ├── models.py            # Pydantic v2 schemas with examples and documentation
│   ├── portal/
│   │   ├── client.py        # PortalClient: auth, session lock, retries, HMAC signing
│   │   ├── devalue.py       # SvelteKit __data.json index-based devalue decoder
│   │   └── parsers.py       # Detail shapes, hierarchy strings, timestamps, numbers
│   ├── services/
│   │   ├── meters.py        # Meter search, detail enrichment, and nearby spatial search
│   │   ├── consumption.py   # Consumption time-series, deltas, and anomaly detection
│   │   ├── transformers.py  # DT aggregations with active meter counts
│   │   └── hierarchy.py     # Tree reconstruction and data quality auditing
│   └── routers/
│       ├── health.py        # GET /health
│       ├── meters.py        # GET /meters, /meters/{id}, /meters/{id}/consumption, /nearby
│       ├── transformers.py  # GET /transformers, /transformers/{code}
│       ├── hierarchy.py     # GET /hierarchy
│       └── admin.py         # POST /admin/refresh
├── tests/
│   ├── conftest.py          # Pytest fixtures and sample path resolution
│   ├── test_devalue.py      # Devalue decoder tests against sample fixtures
│   ├── test_parsers.py      # Detail shapes, timestamps, and delta/anomaly tests
│   ├── test_portal_client.py# Portal client tests with respx mocks (auth, HMAC, retries)
│   ├── test_services.py     # Service logic tests
│   └── test_routers.py      # End-to-end endpoint tests for all routes
├── scripts/
│   └── export_openapi.py   # Exports openapi.json to repository root
├── openapi.json             # Generated OpenAPI 3.1 schema
├── PROTOCOL.md              # Portal protocol analysis and reverse-engineering document
├── REFLECTION.md            # Project reflection placeholders
├── requirements.txt         # Pinned project dependencies
├── pytest.ini               # Pytest configuration
├── .env.example             # Template for environment configuration
└── README.md
```

---

## Installation and Setup

### Prerequisites
- Python 3.11+
- Virtual environment (`venv`)

### 1. Setup Virtual Environment and Install Dependencies

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in credentials:

```bash
cp .env.example .env
```

Configuration parameters:
```env
PORTAL_BASE_URL=https://ops.urja.internal
PORTAL_USERNAME=operator@urja.local
PORTAL_PASSWORD=secret-password

HOST=0.0.0.0
PORT=8000
DEBUG=false

METADATA_CACHE_TTL_SECONDS=900
CONSUMPTION_CACHE_TTL_SECONDS=60

PORTAL_MAX_CONCURRENCY=4
PORTAL_REQUEST_DELAY_SECONDS=0.05
PORTAL_TIMEOUT_SECONDS=10.0
```

### 3. Run the Development Server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive documentation:
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- OpenAPI JSON: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

### 4. Run the Test Suite

All tests run locally using saved sample fixtures in `samples/` and `respx` HTTP mocking. No network calls are made to the live portal during unit testing:

```bash
pytest
```

---

## Sample cURL Requests and Responses

### 1. Search and Filter Meters

```bash
curl -i "http://localhost:8000/meters?make=Genus&phase=single&page=1&page_size=2"
```

Response:
```http
HTTP/1.1 200 OK
content-type: application/json
x-cache-age: 0.1

{
  "items": [
    {
      "meter_id": "J100003",
      "serial_no": "L&84997",
      "make": "Genus",
      "phase_type": "single",
      "installation_status": "Installed",
      "dt_code": "DT-004",
      "installation_type": null
    },
    {
      "meter_id": "J100004",
      "serial_no": "SE65293",
      "make": "Genus",
      "phase_type": "single",
      "installation_status": "Faulty",
      "dt_code": "DT-005",
      "installation_type": null
    }
  ],
  "total": 92,
  "page": 1,
  "page_size": 2
}
```

### 2. Get Meter Detail (Coordinates & Hierarchy)

```bash
curl -i "http://localhost:8000/meters/J100000"
```

Response:
```http
HTTP/1.1 200 OK
content-type: application/json
x-cache-age: 12.4

{
  "meter_id": "J100000",
  "serial_no": "SE33962",
  "make": "HPL",
  "phase_type": "single",
  "installation_status": "Decommissioned",
  "installation_type": "Whole Current",
  "location": {
    "latitude": 26.983252940494662,
    "longitude": 75.88163262899984
  },
  "hierarchy": {
    "zone": {
      "name": "Jaipur Zone 1",
      "code": "Z-01"
    },
    "circle": {
      "name": "Circle 1",
      "code": "C-01"
    },
    "division": {
      "name": "Division 1",
      "code": "D-01"
    },
    "subdivision": {
      "name": "Subdivision 1",
      "code": "SD-01"
    },
    "substation": {
      "name": "Substation 1",
      "code": "SS-01"
    },
    "feeder": {
      "name": "Feeder 1",
      "code": "F-001"
    },
    "dt": {
      "name": "Malviya Nagar DT 1",
      "code": "DT-001"
    }
  }
}
```

### 3. Get Meter Consumption with Interval Deltas and Anomaly Flags

```bash
curl -i "http://localhost:8000/meters/J100000/consumption?from=2026-06-24&to=2026-06-25"
```

Response:
```http
HTTP/1.1 200 OK
content-type: application/json
x-cache-age: 2.1

{
  "meter_id": "J100000",
  "interval": "half_hourly",
  "from_date": "2026-06-24",
  "to_date": "2026-06-25",
  "readings": [
    {
      "timestamp": "2026-06-24T00:00:00+05:30",
      "kwh": 48439.16,
      "kvah": 52314.29,
      "volt_r": 229.0,
      "consumption_kwh": null,
      "anomaly": false
    },
    {
      "timestamp": "2026-06-24T00:30:00+05:30",
      "kwh": 48439.58,
      "kvah": 52314.75,
      "volt_r": 220.0,
      "consumption_kwh": 0.42,
      "anomaly": false
    }
  ]
}
```

### 4. Query Distribution Transformers with Connected Meter Counts

```bash
curl -i "http://localhost:8000/transformers/DT-007"
```

Response:
```http
HTTP/1.1 200 OK
content-type: application/json
x-cache-age: 4.8

{
  "code": "DT-007",
  "name": "Old Malviya Nagar Xfmr",
  "feeder_code": "F-007",
  "capacity_kva": "250",
  "meter_count": 13
}
```

### 5. Standard Error Example (404 and 400)

```bash
curl -i "http://localhost:8000/meters/INVALID_ID"
```

Response:
```http
HTTP/1.1 404 Not Found
content-type: application/json

{
  "error": {
    "code": "NOT_FOUND",
    "message": "Resource not found: /meters/INVALID_ID/__data.json"
  }
}
```

---

## Assumptions

1. **Timezone Offset (`+05:30`)**: Timestamps served by the portal (e.g. `23/06/2026 23:30`) omit timezone information. Given that all observed assets reside in Rajasthan (Jaipur), the service assumes India Standard Time (`Asia/Kolkata`, `UTC+05:30`) and converts all timestamps to standard ISO-8601 strings (e.g., `2026-06-23T23:30:00+05:30`).
2. **Interval Resolution Inference**: The portal offers no metadata flag designating whether a meter logs data half-hourly or daily. Our parser infers the interval dynamically by computing the median time delta between consecutive readings: differences $\le 3600$ seconds classify as `half_hourly`, while differences $\ge 86400$ seconds classify as `daily`.
3. **Hierarchy Graph Structure**: As detailed in PROTOCOL.md, codes inside names do not follow numeric nesting. The distribution hierarchy is built strictly from observed parent-child relationships across meters. Non-standard strings missing code suffixes (e.g., `Circle 5` instead of `Circle 5 (C-05)`) are handled gracefully by setting `code` equal to `name`.
4. **Consumption Deltas & Anomaly Flags**: Active energy (`kwh`) and apparent energy (`kvah`) are cumulative register values. The first reading in any query window establishes the baseline (`consumption_kwh = null`). Subsequent intervals calculate delta = `kwh[i] - kwh[i-1]`. Any negative delta (indicating rollover, register corruption, or meter replacement) is preserved and flagged with `anomaly: true`.
5. **Strict Date Range Validation**: The legacy portal silently falls back to its default 7-day window when passed dates outside `YYYY-MM-DD`. To prevent misleading data, our API strictly enforces `YYYY-MM-DD` and requires `from <= to`, immediately returning `HTTP 400 Bad Request` upon format violations.
6. **Defensive Handling of Unverified Portal Features**: Features marked `(unverified)` in PROTOCOL.md (such as HMAC export key rotation or pagination limits) are handled defensively: signing keys are fetched on demand, cached, and automatically refreshed if the export route returns HTTP 401/403.

---

## Design Decisions and Trade-Offs

- **FastAPI + Async httpx**: FastAPI provides native Pydantic v2 validation, automatic OpenAPI generation, and asynchronous request handling. `httpx.AsyncClient` manages connection pooling, cookies, and non-blocking I/O.
- **In-Memory TTL Caching over External Stores**: An in-memory cache with thread-safe `asyncio.Lock` avoids external dependencies (Redis/Memcached), matching the take-home project scope. Cache entries track exact creation timestamps to expose `X-Cache-Age` in responses.
- **Session Locking and Re-authentication**: Proactive re-login at ~50 minutes prevents session timeout during heavy operations. A reactive retry mechanism intercepts 401s, redirects, and unexpected HTML login pages. `asyncio.Lock` ensures that concurrent requests do not trigger stampeding login requests.
- **Bounded Concurrency Semaphore**: All outgoing portal requests are governed by `asyncio.Semaphore(max_concurrency=4)` with a small politeness sleep (50ms) to avoid overwhelming the legacy portal.

---

## Spatial Search Scaling Analysis (`/meters/nearby`)

The `/meters/nearby` endpoint uses a two-phase spatial search:
1. **Bounding Box Prefilter**: Computes minimum/maximum latitude and longitude bounds using 1° latitude $\approx 111$ km and $\Delta \text{lon} = \frac{\text{radius}}{111 \cdot \cos(\text{lat})}$.
2. **Haversine Distance**: For points inside the bounding box, computes precise great-circle distance and sorts results.

### At What Scale Does This Stop Working?
- **Current Scale (hundreds to 10,000 meters)**: Evaluating 403 meters in memory takes $< 1$ millisecond.
- **Threshold of Breakdown ($\approx 50,000 - 100,000+$ meters)**:
  - In-memory scans evaluate coordinates sequentially or via linear CPU filtering, leading to memory overhead and CPU spikes under high query concurrency.
  - Periodic coordinate enrichment over HTTP becomes prohibitive without a persistent spatial store.

### What to Change for High Scale
1. **Spatial Database (PostGIS)**: Migrate spatial storage to PostgreSQL with PostGIS:
   ```sql
   CREATE INDEX idx_meters_geom ON meters USING GIST (geom);
   SELECT meter_id, ST_Distance(geom, ST_MakePoint(lon, lat)::geography) / 1000 AS distance_km
   FROM meters
   WHERE ST_DWithin(geom, ST_MakePoint(lon, lat)::geography, :radius_meters)
   ORDER BY distance_km;
   ```
2. **Spatial Index in SQLite (`R*Tree` extension)**: For lightweight single-process deployments without external database services, SQLite’s standard R*Tree module allows indexed 2D bounding-box queries.
3. **Geohash / H3 Hexagonal Grid Indexing**: Pre-indexing meter coordinates into Uber H3 spatial indexes enables $O(1)$ spatial cell lookups and radius k-ring expansions.

---

## What Was Intentionally Skipped

- **Heavy Database ORM & Migrations**: Avoided SQLAlchemy/Tortoise ORM to keep dependencies minimal and prevent over-engineering.
- **Docker / Kubernetes Container Orchestration**: Focus remained strictly on a clean, runnable Python service.
- **Scraping Portal HTML**: The legacy portal is an SSR + client-side SvelteKit application whose table markup initialises with 0 rows; data extraction is achieved purely via JSON and devalue endpoints.

---

## What I Would Improve with More Time

- **Persistent Local Spatial Index**: Integrate SQLite's native `rtree` extension to support fast bounding box spatial queries.
- **Background Periodic Syncer**: Implement an automated background task to incrementally refresh the meter and transformer catalog outside the request lifecycle.
- **Prometheus Metrics**: Expose `/metrics` tracking cache hit/miss rates, legacy portal latency, and request retry frequencies.

---

## Reflection

### Assumptions
<!-- TODO: Add personal reflection on domain or technical assumptions made during implementation -->

### Hardest Part
<!-- TODO: Add personal reflection on the most challenging aspect of this project -->

### What I'd Do with Another Day
<!-- TODO: Add personal reflection on enhancements or features you would build with extra time -->

### A Mistake I Made
<!-- TODO: Add personal reflection on any bugs or refactorings encountered during development -->

### What I'd Criticise
<!-- TODO: Add personal reflection offering critical evaluation of the legacy portal architecture or API tradeoffs -->
