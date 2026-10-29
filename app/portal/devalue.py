"""Decoder for SvelteKit __data.json devalue serialization.

SvelteKit page endpoints serialize rich data into a flat array where objects
and arrays reference other slots by integer index. Special negative indices
encode non-JSON primitives:
  -1: undefined (mapped to None)
  -2: NaN (mapped to float('nan'))
  -3: Infinity (mapped to float('inf'))
  -4: -Infinity (mapped to float('-inf'))
  -5: -0 (mapped to -0.0)
"""

from typing import Any


def unflatten(serialized: list[Any], root_index: int = 0) -> Any:
    """Unflatten a devalue-serialized flat array into nested Python objects.

    Handles cycles, nested dicts/lists, primitive values, and special negative codes.
    """
    if not serialized:
        return None

    resolved: dict[int, Any] = {}

    def resolve(idx: int) -> Any:
        if idx == -1:
            return None  # undefined
        elif idx == -2:
            return float("nan")
        elif idx == -3:
            return float("inf")
        elif idx == -4:
            return float("-inf")
        elif idx == -5:
            return -0.0
        elif idx < -5:
            raise ValueError(f"Unknown special devalue code: {idx}")

        if idx >= len(serialized):
            raise IndexError(f"Devalue index out of bounds: {idx} >= {len(serialized)}")

        if idx in resolved:
            return resolved[idx]

        raw = serialized[idx]
        if isinstance(raw, dict):
            out: dict[str, Any] = {}
            resolved[idx] = out
            for k, ref in raw.items():
                if isinstance(ref, int):
                    out[k] = resolve(ref)
                else:
                    out[k] = ref
            return out
        elif isinstance(raw, list):
            out_list: list[Any] = []
            resolved[idx] = out_list
            for ref in raw:
                if isinstance(ref, int):
                    out_list.append(resolve(ref))
                else:
                    out_list.append(ref)
            return out_list
        else:
            resolved[idx] = raw
            return raw

    return resolve(root_index)


def decode_sveltekit_page_data(payload: dict[str, Any] | list[Any], node_index: int = 2) -> dict[str, Any]:
    """Decode SvelteKit __data.json response.

    Extracts the page-data node (default index 2) and unflattens its devalue array.
    """
    if isinstance(payload, list):
        result = unflatten(payload)
        return result if isinstance(result, dict) else {"data": result}

    nodes = payload.get("nodes", [])
    if node_index < len(nodes) and isinstance(nodes[node_index], dict) and "data" in nodes[node_index]:
        data = nodes[node_index]["data"]
        result = unflatten(data)
        if isinstance(result, dict):
            return result

    # Fallback: search nodes for data containing meterId or detail
    for node in nodes:
        if isinstance(node, dict) and "data" in node and isinstance(node["data"], list):
            res = unflatten(node["data"])
            if isinstance(res, dict) and ("meterId" in res or "detail" in res):
                return res

    raise ValueError("Could not find page data node in SvelteKit response")

