"""FastAPI 入口：草稿保存、冗余裁决、裁决失效。"""

from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException

from .models import MAX_FIBERS, MAX_NODES, MIN_FIBERS, MIN_NODES, Draft
from .solver import Path, select_redundant_pair

app = FastAPI(title="展柜报警链路冗余裁决 API")

_lock = threading.Lock()
# 单租户服务：内存中仅保存当前草稿与最近一次裁决。
# 草稿每保存一次版本号 +1；裁决与保存时的草稿版本绑定，
# 草稿一旦修改，旧裁决即失效（/api/adjudicate/latest 返回 409）。
_state: dict = {"draft": None, "draft_version": 0, "adjudication": None}


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
