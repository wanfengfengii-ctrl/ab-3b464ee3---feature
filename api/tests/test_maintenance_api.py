"""检修预案 API 行为测试：联合裁决、分阶段响应、失效与不可行提示。"""

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


# 三条互不共享中间节点的走廊：A（含停用 #1）、B、C
MAINT_DRAFT = {
    "nodes": ["S", "T", "A", "B", "C", "D"],
    "fibers": [
        {"a": "S", "b": "A", "length": 1, "attenuation": 1},   # 1 停用
        {"a": "A", "b": "T", "length": 1, "attenuation": 1},   # 2
        {"a": "S", "b": "B", "length": 2, "attenuation": 1},   # 3
        {"a": "B", "b": "D", "length": 2, "attenuation": 1},   # 4
        {"a": "D", "b": "T", "length": 2, "attenuation": 1},   # 5
        {"a": "S", "b": "C", "length": 3, "attenuation": 1},   # 6
        {"a": "C", "b": "T", "length": 3, "attenuation": 1},   # 7
    ],
    "source": "S",
    "target": "T",
    "attenuation_limit": 1000,
}

# 去掉 #1 后所有 S-T 路径都经过 B：普通双路存在，但无法只切换一路覆盖停纤
INFEASIBLE_MAINT_DRAFT = {
    "nodes": ["S", "T", "A", "B", "C"],
    "fibers": [
        {"a": "S", "b": "A", "length": 1, "attenuation": 1},  # 1 停用
        {"a": "A", "b": "T", "length": 1, "attenuation": 1},  # 2
        {"a": "S", "b": "B", "length": 2, "attenuation": 1},  # 3
        {"a": "B", "b": "C", "length": 2, "attenuation": 1},  # 4
        {"a": "C", "b": "T", "length": 2, "attenuation": 1},  # 5
        {"a": "B", "b": "T", "length": 8, "attenuation": 1},  # 6
        {"a": "A", "b": "B", "length": 3, "attenuation": 1},  # 7
    ],
    "source": "S",
    "target": "T",
    "attenuation_limit": 1000,
}


def _check_plan_shape(body):
    assert set(body) >= {
        "status", "draft_version", "outage_fiber",
        "before", "during", "affected", "resident",
        "alternate", "switched_path", "message",
    }
    assert body["status"] == "ok"
    assert set(body["before"]) == {"affected", "resident"}
    assert set(body["during"]) == {"alternate", "resident"}
    # 驻留路两阶段完全一致（接续点与光纤序列不变）
    assert body["before"]["resident"] == body["during"]["resident"]


def test_plan_without_draft_conflicts():
    resp = client.post("/api/maintenance/plan", json={"outage_fiber": 1})
    assert resp.status_code == 409


def test_outage_fiber_out_of_range_rejected():
    client.put("/api/draft", json=MAINT_DRAFT)
    resp = client.post("/api/maintenance/plan", json={"outage_fiber": 8})
    assert resp.status_code == 422
    resp = client.post("/api/maintenance/plan", json={"outage_fiber": 0})
    assert resp.status_code == 422


def test_successful_maintenance_plan_flow():
    client.put("/api/draft", json=MAINT_DRAFT)
    resp = client.post("/api/maintenance/plan", json={"outage_fiber": 1})
    assert resp.status_code == 200
    body = resp.json()
    _check_plan_shape(body)

    affected, resident, alternate = body["affected"], body["resident"], body["alternate"]

    # 检修前受影响路实际使用停用光纤；停用期间两路都不得使用它
    assert 1 in affected["fibers"]
    assert 1 not in resident["fibers"]
    assert 1 not in alternate["fibers"]
    # 三路两两不同
    assert len({tuple(affected["fibers"]), tuple(resident["fibers"]),
                tuple(alternate["fibers"])}) == 3

    # 检修前双路独立
    assert not (set(affected["fibers"]) & set(resident["fibers"]))
    assert not (set(affected["nodes"][1:-1]) & set(resident["nodes"][1:-1]))
    # 停用期间双路独立
    assert not (set(alternate["fibers"]) & set(resident["fibers"]))
    assert not (set(alternate["nodes"][1:-1]) & set(resident["nodes"][1:-1]))

    # 各阶段线路长度/衰减与明细一致
    for p in (affected, resident, alternate):
        assert p["attenuation"] <= 1000
    assert body["switched_path"]["from"] == affected
    assert body["switched_path"]["to"] == alternate
    assert body["switched_path"]["length_delta"] == alternate["length"] - affected["length"]

    # 联合裁决结果：A=#1,#2；驻留/替代在 B(#3,#4,#5) 与 C(#6,#7) 间按序号分配
    assert affected["fibers"] == [1, 2]
    assert resident["fibers"] == [3, 4, 5]
    assert alternate["fibers"] == [6, 7]

    # latest 返回同一预案
    resp = client.get("/api/maintenance/latest")
    assert resp.status_code == 200
    assert resp.json() == body


def test_latest_without_plan_404():
    client.put("/api/draft", json=MAINT_DRAFT)
    assert client.get("/api/maintenance/latest").status_code == 404


def test_draft_change_invalidates_maintenance_plan():
    client.put("/api/draft", json=MAINT_DRAFT)
    client.post("/api/maintenance/plan", json={"outage_fiber": 1})
    assert client.get("/api/maintenance/latest").status_code == 200

    changed = dict(MAINT_DRAFT, attenuation_limit=999)
    client.put("/api/draft", json=changed)
    resp = client.get("/api/maintenance/latest")
    assert resp.status_code == 409

    # 重新裁决后恢复有效
    resp = client.post("/api/maintenance/plan", json={"outage_fiber": 1})
    assert resp.status_code == 200
    assert client.get("/api/maintenance/latest").status_code == 200


def test_outage_fiber_change_invalidates_old_plan():
    client.put("/api/draft", json=MAINT_DRAFT)
    client.post("/api/maintenance/plan", json={"outage_fiber": 1})
    # 改选目标停用光纤（先登记新选择），旧预案立即失效
    resp = client.put("/api/maintenance/outage", json={"outage_fiber": 2})
    assert resp.status_code == 200
    resp = client.get("/api/maintenance/latest")
    assert resp.status_code == 409

    # 对新停用光纤裁决成功
    resp = client.post("/api/maintenance/plan", json={"outage_fiber": 2})
    assert resp.status_code == 200
    assert resp.json()["outage_fiber"] == 2
    assert client.get("/api/maintenance/latest").status_code == 200


def test_infeasible_maintenance_reports_no_interruption_plan():
    client.put("/api/draft", json=INFEASIBLE_MAINT_DRAFT)
    resp = client.post("/api/maintenance/plan", json={"outage_fiber": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible"
    assert "无法形成无中断检修预案" in body["message"]
    assert body["before"] is None and body["during"] is None
    assert body["affected"] is None and body["resident"] is None
    assert body["alternate"] is None and body["switched_path"] is None


def test_select_outage_without_draft_conflicts():
    resp = client.put("/api/maintenance/outage", json={"outage_fiber": 1})
    assert resp.status_code == 409


def test_select_outage_range_validation():
    client.put("/api/draft", json=MAINT_DRAFT)
    assert client.put("/api/maintenance/outage", json={"outage_fiber": 99}).status_code == 422
