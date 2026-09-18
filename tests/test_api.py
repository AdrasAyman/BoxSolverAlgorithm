import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from boxsolver.entrypoints.api import app

client = TestClient(app)
DATA = Path(__file__).resolve().parent.parent / "data"


def _sample_payload():
    return {
        "Boxes": json.loads((DATA / "boxes.json").read_text()),
        "Items": json.loads((DATA / "items.json").read_text()),
    }


def test_pack_returns_200_with_valid_payload():
    resp = client.post("/v1/pack", json=_sample_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["Success"] is True
    assert len(body["Boxes"]) >= 1


def test_health_endpoint_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_orientations_endpoint_returns_six_codes():
    resp = client.get("/v1/orientations")
    assert resp.status_code == 200
    codes = [o["Code"] for o in resp.json()["Orientations"]]
    assert set(codes) == {"WLD", "LWD", "WDL", "DLW", "LDW", "DWL"}


def test_missing_items_field_returns_422():
    resp = client.post("/v1/pack", json={"Boxes": _sample_payload()["Boxes"]})
    assert resp.status_code == 422


def test_missing_weight_on_item_returns_422():
    payload = _sample_payload()
    del payload["Items"][0]["Weight"]
    resp = client.post("/v1/pack", json=payload)
    assert resp.status_code == 422


def test_negative_dimensions_return_422():
    payload = _sample_payload()
    payload["Items"][0]["Width"] = -10
    resp = client.post("/v1/pack", json=payload)
    assert resp.status_code == 422


def test_empty_boxes_list_returns_422():
    payload = _sample_payload()
    payload["Boxes"] = []
    resp = client.post("/v1/pack", json=payload)
    assert resp.status_code == 422


def test_empty_items_list_returns_422():
    payload = _sample_payload()
    payload["Items"] = []
    resp = client.post("/v1/pack", json=payload)
    assert resp.status_code == 422
