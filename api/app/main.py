"""FastAPI 入口：草稿保存、冗余裁决、检修切换预案、裁决失效。"""

from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException

from .models import MAX_FIBERS, MAX_NODES, MIN_FIBERS, MIN_NODES, Draft, MaintenancePlanRequest
from .solver import MaintenancePlan, Path, plan_maintenance_switch, select_redundant_pair

app = FastAPI(title="展柜报警链路冗余裁决 API")

_lock = threading.Lock()
# 单租户服务：内存中仅保存当前草稿、最近一次裁决与最近一次检修预案。
# 草稿每保存一次版本号 +1；裁决/预案与保存时的草稿版本绑定，
# 草稿一旦修改，旧结果即失效（latest 接口返回 409）。
_state: dict = {
    "draft": None,
    "draft_version": 0,
    "adjudication": None,
    "maintenance": None,
    # 当前选定的计划停用光纤（1 基）；与 maintenance 中绑定的序号不一致时旧预案失效
    "current_outage": None,
}


def _path_payload(path: Path) -> dict:
    return {
        "nodes": list(path.nodes),
        "fibers": [i + 1 for i in path.fibers],  # 对外为 1 基录入序号
        "length": path.length,
        "attenuation": path.attenuation,
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/constraints")
def constraints() -> dict:
    return {
        "min_nodes": MIN_NODES,
        "max_nodes": MAX_NODES,
        "min_fibers": MIN_FIBERS,
        "max_fibers": MAX_FIBERS,
    }


@app.put("/api/draft")
def save_draft(draft: Draft) -> dict:
    """保存草稿；任何字段变化都会使旧裁决与旧检修预案失效。"""
    with _lock:
        _state["draft_version"] += 1
        _state["draft"] = draft
        # 光纤集合可能已变化，原停用光纤序号不再保证语义，需重新选择
        _state["current_outage"] = None
        version = _state["draft_version"]
    return {"status": "saved", "draft_version": version}


@app.get("/api/draft")
def get_draft() -> dict:
    with _lock:
        if _state["draft"] is None:
            raise HTTPException(status_code=404, detail="尚无草稿")
        return {
            "draft_version": _state["draft_version"],
            "draft": _state["draft"].model_dump(),
        }


@app.post("/api/adjudicate")
def adjudicate() -> dict:
    """对当前草稿做冗余双路裁决并记录结果。"""
    with _lock:
        draft = _state["draft"]
        version = _state["draft_version"]
    if draft is None:
        raise HTTPException(status_code=409, detail="尚无草稿，无法裁决")

    result = select_redundant_pair(draft)
    if result is None:
        payload = {
            "status": "infeasible",
            "draft_version": version,
            "message": "无法形成冗余链路：不存在同时满足接续点独立、光纤独立且衰减不超限的主备双路",
            "primary": None,
            "backup": None,
        }
    else:
        primary, backup = result
        payload = {
            "status": "ok",
            "draft_version": version,
            "message": None,
            "primary": _path_payload(primary),
            "backup": _path_payload(backup),
        }
    with _lock:
        _state["adjudication"] = payload
    return payload


@app.get("/api/adjudicate/latest")
def latest_adjudication() -> dict:
    """返回当前草稿对应的裁决；草稿已修改则旧裁决失效，返回 409。"""
    with _lock:
        adjudication = _state["adjudication"]
        version = _state["draft_version"]
    if adjudication is None:
        raise HTTPException(status_code=404, detail="尚无裁决")
    if adjudication["draft_version"] != version:
        raise HTTPException(status_code=409, detail="草稿已修改，旧裁决已失效，请重新提交裁决")
    return adjudication


def _maintenance_payload(plan: MaintenancePlan, version: int, outage_fiber: int) -> dict:
    """组装检修预案响应：同一拓扑中分阶段给出两组线路与驻留/切换标注。"""
    return {
        "status": "ok",
        "draft_version": version,
        "outage_fiber": outage_fiber,
        "before": {
            # 检修前：受影响路（实际经过停纤，停用开始时切出）+ 驻留路（两阶段不变）
            "affected": _path_payload(plan.before_affected),
            "resident": _path_payload(plan.before_resident),
        },
        "during": {
            # 停用期间：替代路（恢复后切回）+ 同一条驻留路
            "alternate": _path_payload(plan.alternate),
            "resident": _path_payload(plan.before_resident),
        },
        # 角色化的三条线路，便于前端直接展示驻留线路与切换线路
        "affected": _path_payload(plan.before_affected),
        "resident": _path_payload(plan.before_resident),
        "alternate": _path_payload(plan.alternate),
        "switched_path": {
            # 仅切换一路：停用开始 affected -> alternate，恢复后 alternate -> affected
            "from": _path_payload(plan.before_affected),
            "to": _path_payload(plan.alternate),
            "length_delta": plan.alternate.length - plan.before_affected.length,
        },
        "message": None,
    }


@app.post("/api/maintenance/plan")
def plan_maintenance(req: MaintenancePlanRequest) -> dict:
    """对当前草稿与选定的计划停用光纤裁决单路切换检修预案并记录结果。

    服务端联合选择检修前受影响路、驻留路与停用期间替代路（穷举三路组合，
    不先裁决普通双路再局部改线）；无法在只切换一路的条件下覆盖停纤时
    返回 infeasible，页面须明确提示无法形成无中断检修预案。
    """
    with _lock:
        draft = _state["draft"]
        version = _state["draft_version"]
    if draft is None:
        raise HTTPException(status_code=409, detail="尚无草稿，无法裁决检修预案")
    fiber_no = req.outage_fiber
    if fiber_no > len(draft.fibers):
        raise HTTPException(
            status_code=422,
            detail=f"停用光纤序号超出范围：当前草稿共 {len(draft.fibers)} 段光纤",
        )

    outage_idx = fiber_no - 1
    plan = plan_maintenance_switch(draft, outage_idx)
    if plan is None:
        payload = {
            "status": "infeasible",
            "draft_version": version,
            "outage_fiber": fiber_no,
            "message": (
                "无法形成无中断检修预案：在只切换一路的条件下，找不到"
                "「检修前实际经过该光纤、停用期间避开它，且驻留路全程不变」"
                "的三条独立线路"
            ),
            "before": None,
            "during": None,
            "affected": None,
            "resident": None,
            "alternate": None,
            "switched_path": None,
        }
    else:
        payload = _maintenance_payload(plan, version, fiber_no)
    with _lock:
        _state["maintenance"] = payload
        _state["current_outage"] = fiber_no
    return payload


@app.put("/api/maintenance/outage")
def select_outage_fiber(req: MaintenancePlanRequest) -> dict:
    """登记计划停用光纤的选择；目标停用光纤改变后旧预案即失效（latest 返回 409）。"""
    with _lock:
        draft = _state["draft"]
    if draft is None:
        raise HTTPException(status_code=409, detail="尚无草稿，无法选择停用光纤")
    if req.outage_fiber > len(draft.fibers):
        raise HTTPException(
            status_code=422,
            detail=f"停用光纤序号超出范围：当前草稿共 {len(draft.fibers)} 段光纤",
        )
    with _lock:
        _state["current_outage"] = req.outage_fiber
    return {"status": "selected", "outage_fiber": req.outage_fiber}


@app.get("/api/maintenance/latest")
def latest_maintenance() -> dict:
    """返回当前草稿/停用光纤对应的检修预案；草稿或目标停用光纤变化后旧预案失效（409）。"""
    with _lock:
        maintenance = _state["maintenance"]
        version = _state["draft_version"]
        current_outage = _state.get("current_outage")
    if maintenance is None:
        raise HTTPException(status_code=404, detail="尚无检修预案")
    if maintenance["draft_version"] != version:
        raise HTTPException(status_code=409, detail="草稿已修改，旧检修预案已失效，请重新裁决")
    if current_outage is not None and maintenance.get("outage_fiber") != current_outage:
        raise HTTPException(status_code=409, detail="计划停用光纤已改变，旧检修预案已失效，请重新裁决")
    return maintenance
