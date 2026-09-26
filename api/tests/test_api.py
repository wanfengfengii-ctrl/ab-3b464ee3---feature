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


# ---------------- 检修切换预案 ----------------


def maintenance_draft():
    """三走廊拓扑：光纤 #1 计划停用时可形成无中断检修预案。"""
    return {
        "nodes": ["J1", "J2", "J3", "J4", "J5", "J6", "J7", "J8"],
        "fibers": [
            {"a": "J1", "b": "J2", "length": 3, "attenuation": 1},
            {"a": "J2", "b": "J3", "length": 3, "attenuation": 1},
            {"a": "J3", "b": "J8", "length": 3, "attenuation": 1},
            {"a": "J1", "b": "J4", "length": 4, "attenuation": 1},
            {"a": "J4", "b": "J5", "length": 4, "attenuation": 1},
            {"a": "J5", "b": "J8", "length": 4, "attenuation": 1},
            {"a": "J1", "b": "J6", "length": 4, "attenuation": 1},
            {"a": "J6", "b": "J7", "length": 4, "attenuation": 1},
            {"a": "J7", "b": "J8", "length": 4, "attenuation": 1},
        ],
        "source": "J1",
        "target": "J8",
        "attenuation_limit": 10,
        "maintenance_fiber": 1,
    }


def test_maintenance_plan_full_flow():
    resp = client.put("/api/draft", json=maintenance_draft())
    assert resp.status_code == 200

    resp = client.post("/api/maintenance/plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["maintenance_fiber"] == 1
    affected, resident, replacement = (
        body["affected"], body["resident"], body["replacement"],
    )

    # 检修前双路实际使用目标光纤；停用期间双路不使用
    assert 1 in affected["fibers"]
    assert 1 not in resident["fibers"]
    assert 1 not in replacement["fibers"]
    # 两阶段各自的中间接续点独立、光纤不复用
    for x, y in ((affected, resident), (replacement, resident)):
        assert not (set(x["nodes"][1:-1]) & set(y["nodes"][1:-1]))
        assert not (set(x["fibers"]) & set(y["fibers"]))
    # 任一阶段每路衰减不超限
    for p in (affected, resident, replacement):
        assert p["attenuation"] <= 10
    # 受影响路为走廊 A，驻留路与替代路为走廊 B、C
    assert affected["fibers"] == [1, 2, 3]
    assert resident["fibers"] == [4, 5, 6]
    assert replacement["fibers"] == [7, 8, 9]

    # 草稿未变，latest 返回同一预案
    resp = client.get("/api/maintenance/plan/latest")
    assert resp.status_code == 200
    assert resp.json() == body


def test_maintenance_plan_invalidated_by_draft_change():
    client.put("/api/draft", json=maintenance_draft())
    client.post("/api/maintenance/plan")
    assert client.get("/api/maintenance/plan/latest").status_code == 200

    # 修改草稿其他字段（调低上限），旧预案失效
    changed = maintenance_draft()
    changed["attenuation_limit"] = 9
    client.put("/api/draft", json=changed)
    assert client.get("/api/maintenance/plan/latest").status_code == 409

    # 重新生成后恢复有效
    assert client.post("/api/maintenance/plan").status_code == 200
    assert client.get("/api/maintenance/plan/latest").status_code == 200


def test_maintenance_plan_invalidated_by_target_fiber_change():
    client.put("/api/draft", json=maintenance_draft())
    client.post("/api/maintenance/plan")
    assert client.get("/api/maintenance/plan/latest").status_code == 200

    # 仅改目标停用光纤，旧预案同样失效
    changed = maintenance_draft()
    changed["maintenance_fiber"] = 4
    client.put("/api/draft", json=changed)
    assert client.get("/api/maintenance/plan/latest").status_code == 409

    # 对新目标光纤重新生成
    resp = client.post("/api/maintenance/plan")
    assert resp.status_code == 200
    assert resp.json()["maintenance_fiber"] == 4
    assert client.get("/api/maintenance/plan/latest").status_code == 200


def test_maintenance_plan_without_draft():
    assert client.post("/api/maintenance/plan").status_code == 409


def test_maintenance_plan_requires_selected_fiber():
    draft = maintenance_draft()
    del draft["maintenance_fiber"]
    client.put("/api/draft", json=draft)
    resp = client.post("/api/maintenance/plan")
    assert resp.status_code == 409


@pytest.mark.parametrize("fiber_no", [0, -1, 10, 99])
def test_maintenance_fiber_out_of_range_rejected(fiber_no):
    draft = maintenance_draft()
    draft["maintenance_fiber"] = fiber_no
    resp = client.put("/api/draft", json=draft)
    assert resp.status_code == 422


def test_maintenance_plan_infeasible_reports_cannot_cover_outage():
    # 样例拓扑对光纤 #5 无法只切换一路覆盖停纤
    draft = sample_draft()
    draft["maintenance_fiber"] = 5
    client.put("/api/draft", json=draft)
    resp = client.post("/api/maintenance/plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible"
    assert "无法形成无中断检修预案" in body["message"]
    assert body["resident"] is None
    assert body["affected"] is None
    assert body["replacement"] is None


def test_maintenance_latest_without_any_plan():
    assert client.get("/api/maintenance/plan/latest").status_code == 404
