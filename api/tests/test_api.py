"""API 行为测试：健康检查、草稿校验、裁决、失效。"""

import pytest
from fastapi.testclient import TestClient

from app.main import _lock, _state, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    with _lock:
        _state["draft"] = None
        _state["draft_version"] = 0
        _state["adjudication"] = None
        _state["maintenance"] = None
        _state["current_outage"] = None
    yield


def sample_draft():
    return {
        "nodes": ["J1", "J2", "J3", "J4", "J5", "J6"],
        "fibers": [
            {"a": "J1", "b": "J2", "length": 4, "attenuation": 1},
            {"a": "J2", "b": "J4", "length": 5, "attenuation": 1},
            {"a": "J4", "b": "J6", "length": 4, "attenuation": 2},
            {"a": "J1", "b": "J3", "length": 3, "attenuation": 2},
            {"a": "J3", "b": "J5", "length": 6, "attenuation": 1},
            {"a": "J5", "b": "J6", "length": 3, "attenuation": 1},
            {"a": "J2", "b": "J3", "length": 2, "attenuation": 1},
            {"a": "J4", "b": "J5", "length": 3, "attenuation": 1},
        ],
        "source": "J1",
        "target": "J6",
        "attenuation_limit": 10,
    }


def infeasible_draft():
    return {
        "nodes": ["S", "T", "M", "C", "D"],
        "fibers": [
            {"a": "S", "b": "M", "length": 1, "attenuation": 1},
            {"a": "M", "b": "T", "length": 1, "attenuation": 1},
            {"a": "S", "b": "C", "length": 1, "attenuation": 1},
            {"a": "C", "b": "D", "length": 1, "attenuation": 1},
            {"a": "D", "b": "S", "length": 1, "attenuation": 1},
            {"a": "C", "b": "M", "length": 1, "attenuation": 1},
            {"a": "D", "b": "M", "length": 1, "attenuation": 1},
        ],
        "source": "S",
        "target": "T",
        "attenuation_limit": 100,
    }


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_constraints():
    resp = client.get("/api/constraints")
    assert resp.status_code == 200
    assert resp.json() == {
        "min_nodes": 5, "max_nodes": 9, "min_fibers": 7, "max_fibers": 15,
    }


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(nodes=d["nodes"][:4]),                 # 接续点不足 5 个
    lambda d: d.update(nodes=d["nodes"] + ["J7", "J8", "J9", "J10"]),  # 超过 9 个
    lambda d: d.update(fibers=d["fibers"][:6]),               # 光纤不足 7 段
    lambda d: d["fibers"][0].update(length=0),                # 长度非正
    lambda d: d["fibers"][0].update(length=1.5),              # 长度非整数
    lambda d: d["fibers"][0].update(attenuation=-1),          # 衰减为负
    lambda d: d["fibers"][0].update(a="J1", b="J1"),          # 光纤自环
    lambda d: d["fibers"][0].update(a="UNKNOWN"),             # 端点未录入
    lambda d: d.update(source="J6", target="J6"),             # 主控室=展柜
    lambda d: d.update(source="UNKNOWN"),                     # 主控室未录入
    lambda d: d.update(attenuation_limit=-1),                 # 上限为负
    lambda d: d["nodes"].__setitem__(1, "J1"),                # 接续点重名
])
def test_draft_validation_rejected(mutate):
    draft = sample_draft()
    mutate(draft)
    resp = client.put("/api/draft", json=draft)
    assert resp.status_code == 422


def test_adjudicate_without_draft():
    resp = client.post("/api/adjudicate")
    assert resp.status_code == 409


def test_full_adjudication_flow():
    draft = sample_draft()
    resp = client.put("/api/draft", json=draft)
    assert resp.status_code == 200
    assert resp.json()["draft_version"] == 1

    resp = client.post("/api/adjudicate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    primary, backup = body["primary"], body["backup"]

    # 主路不长于备路
    assert primary["length"] <= backup["length"]
    # 除两端外不共享接续点
    p_internal = set(primary["nodes"][1:-1])
    b_internal = set(backup["nodes"][1:-1])
    assert not (p_internal & b_internal)
    assert primary["nodes"][0] == backup["nodes"][0] == "J1"
    assert primary["nodes"][-1] == backup["nodes"][-1] == "J6"
    # 不复用光纤（1 基录入序号）
    assert not (set(primary["fibers"]) & set(backup["fibers"]))
    # 各自衰减不超限
    assert primary["attenuation"] <= 10
    assert backup["attenuation"] <= 10
    # 光纤序号有效
    for f in primary["fibers"] + backup["fibers"]:
        assert 1 <= f <= len(draft["fibers"])

    # 草稿未变，latest 返回同一裁决
    resp = client.get("/api/adjudicate/latest")
    assert resp.status_code == 200
    assert resp.json() == body


def test_draft_change_invalidates_adjudication():
    client.put("/api/draft", json=sample_draft())
    client.post("/api/adjudicate")
    assert client.get("/api/adjudicate/latest").status_code == 200

    # 修改任一字段（此处调低上限），旧裁决立即失效
    changed = sample_draft()
    changed["attenuation_limit"] = 9
    client.put("/api/draft", json=changed)
    resp = client.get("/api/adjudicate/latest")
    assert resp.status_code == 409

    # 重新裁决后恢复有效
    resp = client.post("/api/adjudicate")
    assert resp.status_code == 200
    assert client.get("/api/adjudicate/latest").status_code == 200


def test_infeasible_reports_cannot_form_redundant_link():
    client.put("/api/draft", json=infeasible_draft())
    resp = client.post("/api/adjudicate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible"
    assert body["primary"] is None and body["backup"] is None
    assert "无法形成冗余链路" in body["message"]


def test_latest_without_any_adjudication():
    assert client.get("/api/adjudicate/latest").status_code == 404
