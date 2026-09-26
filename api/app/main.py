"""FastAPI 入口：草稿保存、冗余裁决、检修切换预案、结果失效。"""

from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException

from .models import MAX_FIBERS, MAX_NODES, MIN_FIBERS, MIN_NODES, Draft
from .solver import Path, select_maintenance_plan, select_redundant_pair

app = FastAPI(title="展柜报警链路冗余裁决 API")

_lock = threading.Lock()
# 单租户服务：内存中仅保存当前草稿、最近一次裁决与最近一次检修预案。
# 草稿每保存一次版本号 +1；裁决/预案与保存时的草稿版本绑定，
# 草稿一旦修改（含目标停用光纤字段），旧裁决与旧预案即失效
# （/api/adjudicate/latest 与 /api/maintenance/plan/latest 返回 409）。
_state: dict = {
    "draft": None,
    "draft_version": 0,
    "adjudication": None,
    "maintenance": None,
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
    """保存草稿；任何字段变化都会使旧裁决失效。"""
    with _lock:
        _state["draft_version"] += 1
        _state["draft"] = draft
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


@app.post("/api/maintenance/plan")
def maintenance_plan() -> dict:
    """对当前草稿中计划停用的光纤联合裁决检修切换预案并记录结果。

    预案满足：检修前双路实际使用目标光纤；停用期间双路不使用它；
    两阶段中驻留路完整接续点与光纤序列不变，仅切换另一路；
    任一阶段均满足接续点独立、光纤不复用与衰减上限。
    """
    with _lock:
        draft = _state["draft"]
        version = _state["draft_version"]
    if draft is None:
        raise HTTPException(status_code=409, detail="尚无草稿，无法生成检修预案")
    if draft.maintenance_fiber is None:
        raise HTTPException(status_code=409, detail="尚未选择计划检修停用的光纤，无法生成检修预案")

    fiber_no = draft.maintenance_fiber
    plan = select_maintenance_plan(draft, fiber_no - 1)
    if plan is None:
        payload = {
            "status": "infeasible",
            "draft_version": version,
            "maintenance_fiber": fiber_no,
            "message": (
                "无法形成无中断检修预案：在只切换一路的条件下，不存在检修前实际使用该光纤、"
                "停用期间不使用该光纤，且两阶段均满足接续点独立、光纤独立与衰减上限的双路组合"
            ),
            "resident": None,
            "affected": None,
            "replacement": None,
        }
    else:
        payload = {
            "status": "ok",
            "draft_version": version,
            "maintenance_fiber": fiber_no,
            "message": None,
            "resident": _path_payload(plan.resident),
            "affected": _path_payload(plan.affected),
            "replacement": _path_payload(plan.replacement),
        }
    with _lock:
        _state["maintenance"] = payload
    return payload


@app.get("/api/maintenance/plan/latest")
def latest_maintenance_plan() -> dict:
    """返回当前草稿对应的检修预案；草稿或目标停用光纤已修改则旧预案失效，返回 409。"""
    with _lock:
        plan = _state["maintenance"]
        version = _state["draft_version"]
    if plan is None:
        raise HTTPException(status_code=404, detail="尚无检修预案")
    if plan["draft_version"] != version:
        raise HTTPException(
            status_code=409,
            detail="草稿或目标停用光纤已修改，旧检修预案已失效，请重新生成",
        )
    return plan
