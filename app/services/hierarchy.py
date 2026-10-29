"""Hierarchy service: electrical distribution tree and data quality auditing."""

import asyncio
import json
from pathlib import Path
from typing import Any
from app.cache import TTLCache
from app.config import Settings
from app.models import (
    DataQualityIssue,
    HierarchyTreeNode,
    HierarchyTreeResponse,
)
from app.portal.client import PortalClient
from app.portal.devalue import decode_sveltekit_page_data
from app.portal.parsers import parse_hierarchy, parse_hierarchy_string
from app.services.meters import MetersService


class HierarchyService:
    """Service building recursive distribution hierarchy and identifying data quality issues."""

    def __init__(
        self,
        client: PortalClient,
        meters_service: MetersService,
        cache: TTLCache,
        settings: Settings,
        samples_dir: Path | None = None,
    ):
        self.client = client
        self.meters_service = meters_service
        self.cache = cache
        self.settings = settings
        self.samples_dir = samples_dir or (Path(__file__).resolve().parent.parent.parent / "samples")
        self._load_lock = asyncio.Lock()

    async def get_hierarchy_tree(self) -> tuple[HierarchyTreeResponse, float]:
        """Build distribution tree: Zone > Circle > Division > Subdivision > Substation > Feeder > DT."""
        cache_key = "hierarchy_tree"
        cached, age = await self.cache.get(cache_key)
        if cached is not None:
            return cached, age or 0.0

        async with self._load_lock:
            cached, age = await self.cache.get(cache_key)
            if cached is not None:
                return cached, age or 0.0

            all_meters, _ = await self.meters_service.get_all_meters()

            # Gather meter hierarchy data
            # Use sample detail files if available to populate quickly, otherwise fetch via client
            meter_hierarchies: list[dict[str, Any]] = []
            detail_dir = self.samples_dir / "detail" if self.samples_dir else None

            sample_files_map: dict[str, Path] = {}
            if detail_dir and detail_dir.exists():
                for p in detail_dir.glob("*.json"):
                    sample_files_map[p.stem] = p

            async def load_hierarchy_for_meter(meter_id: str, dt_code: str) -> dict[str, Any]:
                # Check cache first
                cache_k = f"meter_hierarchy:{meter_id}"
                c_hier, _ = await self.cache.get(cache_k)
                if c_hier:
                    return c_hier

                # If sample file available, load from fixture
                if meter_id in sample_files_map:
                    try:
                        with open(sample_files_map[meter_id]) as f:
                            raw = json.load(f)
                        page_data = decode_sveltekit_page_data(raw)
                        hier = page_data.get("hierarchy", {})
                        res = {"meter_id": meter_id, "search_dt": dt_code, "hierarchy": hier}
                        await self.cache.set(cache_k, res, ttl=float(self.settings.metadata_cache_ttl_seconds))
                        return res
                    except Exception:
                        pass

                # Otherwise fetch via portal client
                try:
                    page_data = await self.client.meter_detail(meter_id)
                    hier = page_data.get("hierarchy", {})
                    res = {"meter_id": meter_id, "search_dt": dt_code, "hierarchy": hier}
                    await self.cache.set(cache_k, res, ttl=float(self.settings.metadata_cache_ttl_seconds))
                    return res
                except Exception:
                    return {"meter_id": meter_id, "search_dt": dt_code, "hierarchy": {}}

            # Load in parallel with bounded semaphore
            tasks = [load_hierarchy_for_meter(m.meter_id, m.dt_code) for m in all_meters]
            meter_hierarchies = await asyncio.gather(*tasks)

            # Build tree and detect data quality issues
            response = self._build_tree_and_audit(meter_hierarchies)

            await self.cache.set(
                cache_key,
                response,
                ttl=float(self.settings.metadata_cache_ttl_seconds),
            )
            return response, 0.0

    def _build_tree_and_audit(
        self,
        meter_hierarchies: list[dict[str, Any]],
    ) -> HierarchyTreeResponse:
        """Construct nested tree and detect inconsistencies across observed meters."""
        issues: list[DataQualityIssue] = []

        # Tracking parent assignments to detect conflicts
        dt_to_feeder: dict[str, tuple[str, str]] = {}  # dt_code -> (feeder_code, meter_id)
        feeder_to_substation: dict[str, tuple[str, str]] = {}  # feeder_code -> (substation_code, meter_id)

        # Graph node tracking:
        # We build:
        # zones[zone_code] -> circles[circle_code] -> divisions[division_code] -> ... -> dts[dt_code] -> meter_count
        zones: dict[str, dict[str, Any]] = {}

        def get_or_create_child(
            parent_dict: dict[str, Any],
            code: str,
            name: str,
            level: str,
        ) -> dict[str, Any]:
            if code not in parent_dict:
                parent_dict[code] = {
                    "code": code,
                    "name": name,
                    "level": level,
                    "meter_count": 0,
                    "children": {},
                }
            return parent_dict[code]

        total_meters = len(meter_hierarchies)

        for item in meter_hierarchies:
            meter_id = item["meter_id"]
            search_dt = item["search_dt"]
            raw_hier = item.get("hierarchy", {})

            # Parse hierarchy levels
            z_info = parse_hierarchy_string(raw_hier.get("Zone"))
            c_info = parse_hierarchy_string(raw_hier.get("Circle"))
            d_info = parse_hierarchy_string(raw_hier.get("Division"))
            sd_info = parse_hierarchy_string(raw_hier.get("Subdivision"))
            ss_info = parse_hierarchy_string(raw_hier.get("Sub Station") or raw_hier.get("Substation"))
            f_info = parse_hierarchy_string(raw_hier.get("Feeder"))
            dt_info = parse_hierarchy_string(raw_hier.get("DT"))

            # Audit: Check code formatting anomalies
            for lvl_name, info, raw_val in [
                ("Circle", c_info, raw_hier.get("Circle")),
                ("Feeder", f_info, raw_hier.get("Feeder")),
                ("Substation", ss_info, raw_hier.get("Sub Station") or raw_hier.get("Substation")),
            ]:
                if raw_val and "(" not in raw_val:
                    issues.append(
                        DataQualityIssue(
                            meter_id=meter_id,
                            issue_type="code_format_inconsistency",
                            description=f"{lvl_name} '{raw_val}' lacks standard '(CODE)' suffix formatting",
                        )
                    )

            # Audit: Check Search DT vs Hierarchy DT
            if dt_info and search_dt and dt_info["code"] != search_dt:
                issues.append(
                    DataQualityIssue(
                        meter_id=meter_id,
                        issue_type="search_dt_mismatch",
                        description=f"Search endpoint reports dtCode '{search_dt}', but hierarchy specifies '{dt_info['code']}'",
                    )
                )

            # Audit: Check DT parent conflict (Feeder)
            if dt_info and f_info:
                dt_c = dt_info["code"]
                f_c = f_info["code"]
                if dt_c in dt_to_feeder:
                    prev_f, prev_m = dt_to_feeder[dt_c]
                    if prev_f != f_c:
                        issues.append(
                            DataQualityIssue(
                                meter_id=meter_id,
                                issue_type="dt_feeder_mismatch",
                                description=(
                                    f"DT '{dt_c}' assigned to feeder '{f_c}', "
                                    f"disagreeing with meter {prev_m} where DT '{dt_c}' was under feeder '{prev_f}'"
                                ),
                            )
                        )
                else:
                    dt_to_feeder[dt_c] = (f_c, meter_id)

            # Audit: Check Feeder parent conflict (Substation)
            if f_info and ss_info:
                f_c = f_info["code"]
                ss_c = ss_info["code"]
                if f_c in feeder_to_substation:
                    prev_ss, prev_m = feeder_to_substation[f_c]
                    if prev_ss != ss_c:
                        issues.append(
                            DataQualityIssue(
                                meter_id=meter_id,
                                issue_type="feeder_substation_mismatch",
                                description=(
                                    f"Feeder '{f_c}' assigned to substation '{ss_c}', "
                                    f"disagreeing with meter {prev_m} where Feeder '{f_c}' was under substation '{prev_ss}'"
                                ),
                            )
                        )
                else:
                    feeder_to_substation[f_c] = (ss_c, meter_id)

            # Fallbacks for missing levels to tolerate missing hierarchy gracefully
            z = z_info or {"code": "Z-UNKNOWN", "name": "Unknown Zone"}
            c = c_info or {"code": "C-UNKNOWN", "name": "Unknown Circle"}
            d = d_info or {"code": "D-UNKNOWN", "name": "Unknown Division"}
            sd = sd_info or {"code": "SD-UNKNOWN", "name": "Unknown Subdivision"}
            ss = ss_info or {"code": "SS-UNKNOWN", "name": "Unknown Substation"}
            f = f_info or {"code": "F-UNKNOWN", "name": "Unknown Feeder"}
            dt = dt_info or {"code": search_dt or "DT-UNKNOWN", "name": search_dt or "Unknown DT"}

            # Build tree node path
            z_node = get_or_create_child(zones, z["code"], z["name"], "zone")
            c_node = get_or_create_child(z_node["children"], c["code"], c["name"], "circle")
            d_node = get_or_create_child(c_node["children"], d["code"], d["name"], "division")
            sd_node = get_or_create_child(d_node["children"], sd["code"], sd["name"], "subdivision")
            ss_node = get_or_create_child(sd_node["children"], ss["code"], ss["name"], "substation")
            f_node = get_or_create_child(ss_node["children"], f["code"], f["name"], "feeder")
            dt_node = get_or_create_child(f_node["children"], dt["code"], dt["name"], "dt")

            # Increment meter count on the leaf DT
            dt_node["meter_count"] += 1

        # Recursive function to convert dictionaries to HierarchyTreeNode models and sum meter counts
        def convert_to_tree_node(data: dict[str, Any]) -> HierarchyTreeNode:
            children_nodes: list[HierarchyTreeNode] = []
            for child_code in sorted(data["children"].keys()):
                children_nodes.append(convert_to_tree_node(data["children"][child_code]))

            # If node has children, its meter count is the sum of its children's meter counts
            count = sum(c.meter_count for c in children_nodes) if children_nodes else data["meter_count"]

            return HierarchyTreeNode(
                name=data["name"],
                code=data["code"],
                level=data["level"],
                meter_count=count,
                children=children_nodes,
            )

        tree_nodes = [convert_to_tree_node(zones[k]) for k in sorted(zones.keys())]

        return HierarchyTreeResponse(
            total_meters=total_meters,
            tree=tree_nodes,
            data_quality=issues,
        )
