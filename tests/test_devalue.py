import glob
import json
import math
from pathlib import Path
import pytest
from app.portal.devalue import decode_sveltekit_page_data, unflatten


def test_unflatten_primitives_and_negative_codes():
    # Special negative codes
    # -1: None (undefined)
    # -2: NaN
    # -3: inf
    # -4: -inf
    # -5: -0.0
    serialized = [
        {"a": -1, "b": -2, "c": -3, "d": -4, "e": -5, "f": 1},
        "hello",
    ]
    res = unflatten(serialized)
    assert res["a"] is None
    assert math.isnan(res["b"])
    assert math.isinf(res["c"]) and res["c"] > 0
    assert math.isinf(res["d"]) and res["d"] < 0
    assert res["e"] == 0.0 and math.copysign(1.0, res["e"]) == -1.0
    assert res["f"] == "hello"


def test_unflatten_cycles():
    # Object referencing itself
    # Index 0 is {"self": 0}
    serialized = [{"self": 0}]
    res = unflatten(serialized)
    assert res["self"] is res

    # Mutual recursion: 0 references 1, 1 references 0
    serialized_mutual = [{"other": 1}, {"parent": 0}]
    res_mutual = unflatten(serialized_mutual)
    assert res_mutual["other"]["parent"] is res_mutual


def test_unflatten_nested_lists_and_dicts():
    serialized = [
        {"items": 1, "title": 2},
        [3, 4],
        "grocery list",
        "apples",
        "oranges",
    ]
    res = unflatten(serialized)
    assert res == {
        "title": "grocery list",
        "items": ["apples", "oranges"],
    }


def test_unflatten_sample_detail_files(detail_samples_dir: Path):
    sample_files = list(detail_samples_dir.glob("*.json"))
    assert len(sample_files) > 100

    for path in sample_files:
        with open(path) as f:
            data = json.load(f)
        page_data = decode_sveltekit_page_data(data)
        assert isinstance(page_data, dict)
        assert "meterId" in page_data
        assert "detail" in page_data
        assert "hierarchy" in page_data
        assert page_data["meterId"].startswith("J1")


def test_decode_sveltekit_fallback():
    # Fallback to search if not at index 2
    data = {
        "type": "data",
        "nodes": [
            None,
            {
                "type": "data",
                "data": [
                    {"meterId": 1, "detail": 2},
                    "J999999",
                    {"data": 3},
                    [],
                ],
            },
        ],
    }
    decoded = decode_sveltekit_page_data(data)
    assert decoded["meterId"] == "J999999"


def test_unflatten_invalid_index():
    with pytest.raises(IndexError):
        unflatten([{"a": 99}])

    with pytest.raises(ValueError):
        unflatten([{"a": -99}])

