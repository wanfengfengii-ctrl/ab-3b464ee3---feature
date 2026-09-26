"""API 冒烟测试：对 compose 网络中真实运行的 api/web 服务发起请求。

- API_URL：API 服务地址（默认 http://api:8000）
- WEB_URL：Web 服务地址（默认 http://web），业务流经由 nginx 代理访问 /api/*，
  与前端页面的真实调用路径一致。
任一断言失败即以非零码退出。
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = os.environ.get("API_URL", "http://api:8000")
WEB_URL = os.environ.get("WEB_URL", "http://web")

FAILED = []


def check(name, cond, extra=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def request(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def request_json(method, url, body=None):
    status, raw = request(method, url, body)
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, None


FEASIBLE_DRAFT = {
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

INFEASIBLE_DRAFT = {
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


def main():
    print(f"API_URL={API_URL}  WEB_URL={WEB_URL}")

    print("-- 健康检查 --")
    status, body = request_json("GET", f"{API_URL}/api/health")
    check("API /api/health 返回 200 ok", status == 200 and body == {"status": "ok"}, f"got {status} {body}")
    status, raw = request("GET", f"{WEB_URL}/health")
    check("Web /health 返回 200", status == 200, f"got {status}")
    status, raw = request("GET", f"{WEB_URL}/")
    check("Web 首页可访问且包含页面标题", status == 200 and "冗余路由裁决" in raw, f"got {status}")
    status, raw = request("GET", f"{WEB_URL}/app.js")
    check("Web 静态资源 app.js 可访问", status == 200 and "adjudicate" in raw, f"got {status}")
    status, raw = request("GET", f"{WEB_URL}/styles.css")
    check("Web 静态资源 styles.css 可访问", status == 200, f"got {status}")

    print("-- 经由 Web 代理的裁决流程（与前端调用路径一致） --")
    api = WEB_URL + "/api"

    status, body = request_json("PUT", f"{api}/draft", FEASIBLE_DRAFT)
    check("保存可行草稿", status == 200 and body.get("status") == "saved", f"got {status} {body}")

    status, body = request_json("POST", f"{api}/adjudicate")
    check("裁决返回 200", status == 200, f"got {status} {body}")
    ok = status == 200 and body.get("status") == "ok"
    check("可行草稿裁决状态为 ok", ok, f"got {body}")
    if ok:
        p, b = body["primary"], body["backup"]
        check("主路不长于备路", p["length"] <= b["length"])
        check("两路除两端外不共享接续点",
              not (set(p["nodes"][1:-1]) & set(b["nodes"][1:-1])),
              f"{p['nodes']} vs {b['nodes']}")
        check("两路不复用同一光纤",
              not (set(p["fibers"]) & set(b["fibers"])),
              f"{p['fibers']} vs {b['fibers']}")
        check("两路各自衰减不超限",
              p["attenuation"] <= 10 and b["attenuation"] <= 10)
        check("路径端点正确",
              p["nodes"][0] == b["nodes"][0] == "J1" and p["nodes"][-1] == b["nodes"][-1] == "J6")
        check("最优解：较长一路长度为 13",
              max(p["length"], b["length"]) == 13, f"got {max(p['length'], b['length'])}")

    status, body = request_json("GET", f"{api}/adjudicate/latest")
    check("草稿未变时 latest 返回 200", status == 200, f"got {status}")

    print("-- 草稿修改后旧裁决失效 --")
    changed = dict(FEASIBLE_DRAFT, attenuation_limit=9)
    status, _ = request_json("PUT", f"{api}/draft", changed)
    check("修改草稿保存成功", status == 200, f"got {status}")
    status, body = request_json("GET", f"{api}/adjudicate/latest")
    check("旧裁决已失效（latest 返回 409）", status == 409, f"got {status} {body}")
    status, body = request_json("POST", f"{api}/adjudicate")
    check("重新裁决恢复有效", status == 200 and body.get("status") == "ok", f"got {status} {body}")

    print("-- 不可行场景必须明确报告无法形成冗余链路 --")
    status, _ = request_json("PUT", f"{api}/draft", INFEASIBLE_DRAFT)
    check("保存不可行草稿", status == 200, f"got {status}")
    status, body = request_json("POST", f"{api}/adjudicate")
    check("裁决状态为 infeasible",
          status == 200 and body.get("status") == "infeasible", f"got {status} {body}")
    check("返回信息包含『无法形成冗余链路』",
          isinstance(body, dict) and "无法形成冗余链路" in (body.get("message") or ""))
    check("infeasible 时不返回主备路",
          isinstance(body, dict) and body.get("primary") is None and body.get("backup") is None)

    print("-- 录入约束校验 --")
    bad = dict(FEASIBLE_DRAFT, nodes=FEASIBLE_DRAFT["nodes"][:4])
    status, _ = request_json("PUT", f"{api}/draft", bad)
    check("接续点少于 5 个被拒绝（422）", status == 422, f"got {status}")
    bad = dict(FEASIBLE_DRAFT, fibers=FEASIBLE_DRAFT["fibers"][:6])
    status, _ = request_json("PUT", f"{api}/draft", bad)
    check("光纤少于 7 段被拒绝（422）", status == 422, f"got {status}")

    print()
    if FAILED:
        print(f"冒烟测试失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
