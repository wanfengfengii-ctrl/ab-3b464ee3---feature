# 展柜报警链路冗余路由裁决

馆藏库房将恒湿展柜的报警数据接入主控室时，需要一条主路与一条备路，避免两条线路
在同一接续盒或同一光缆受损后同时中断。本系统提供网页录入、服务端裁决与结果展示：

- 录入 **5–9 个接续点**、**7–15 段可用光纤**（整数长度、整数衰减），并指定主控室与展柜；
- 提交裁决后，前端调用真实业务 API，在拓扑图与明细中展示主路、备路、各自经过的
  接续点、光纤录入序号、总长度与总衰减；
- 两路除两端（主控室、展柜）外**不共享接续点**、**不复用同一光纤**，且**各自衰减
  均不超过填写的上限**；
- 不存在满足条件的双路时，页面明确显示**无法形成冗余链路**，绝不把共享接续点或
  复用光纤的路线冒充为备路；
- 修改任一草稿字段后，旧裁决立即失效（前端本地作废 + 服务端版本校验双重保证）。

## 裁决规则（服务端穷举，非贪心）

服务端枚举主控室到展柜的全部衰减不超限的简单路径，再遍历所有满足
「中间接续点互不重叠、光纤互不复用」的路径对，按以下关键字**依次**取最小：

1. **较长一路的总长度**最小；
2. **两路衰减之和**最小；
3. 主路的光纤录入序号序列（按行进方向）字典序最小，再比较备路的序号序列。

其中主路 = 较短一路；长度相同取衰减较小者；再相同取序号序列字典序较小者。
算法不会先贪心定下主路再配套路（例如全局最短路会占住所有备用接续点时，
系统会放弃它而选择整体更优的双路组合，见 `api/tests/test_solver.py`）。

## 目录结构

```
├── docker-compose.yml      # api / web / verify 三个服务
├── api/                    # FastAPI 后端（裁决算法、草稿与裁决状态）
│   ├── app/{main,models,solver}.py
│   ├── tests/              # pytest 单元与 API 测试
│   └── Dockerfile
├── web/                    # 静态前端（nginx 托管，/api 反代到 api）
│   ├── src/{index.html,app.js,styles.css}
│   ├── nginx.conf
│   └── Dockerfile
└── verify/                 # 一次性验证服务：测试 + 构建 + API 冒烟
    ├── run.sh / smoke.py
    └── Dockerfile
```

## 运行

```bash
docker compose up --build
```

- Web 页面：http://localhost:8080 （宿主机端口由 `WEB_PORT` 配置，默认 8080）
- API：http://localhost:8000/api/health （宿主机端口由 `API_PORT` 配置，默认 8000）

自定义宿主机端口：

```bash
WEB_PORT=9000 API_PORT=9001 docker compose up --build
```

Web 与 API 均配置了健康检查（`docker compose ps` 可见 healthy 状态）：

- API：`GET /api/health`
- Web：`GET /health`（nginx 直接应答）

## 一键验证（verify 一次性服务）

```bash
docker compose up --build --exit-code-from verify verify
echo $?   # 0 = 全部通过；非 0 = 失败
```

`verify` 会依次完成：

1. **代码测试**：`pytest` 运行 `api/tests`（算法、约束校验、裁决失效等）；
2. **构建校验**：编译后端源码并导入应用；
3. **API 冒烟**：对 compose 网络中真实运行的服务发起请求——健康检查、
   经 nginx 代理的完整裁决流程、草稿修改后旧裁决失效（409）、不可行场景
   必须返回「无法形成冗余链路」、录入约束 422 校验等。

完成后 `verify` 自行退出，退出码即验证结果。

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/constraints` | 录入约束（5–9 接续点、7–15 光纤） |
| PUT | `/api/draft` | 保存草稿；任何字段变化使旧裁决失效（版本号 +1） |
| GET | `/api/draft` | 查看当前草稿 |
| POST | `/api/adjudicate` | 对当前草稿裁决，返回主备双路或 `infeasible` |
| GET | `/api/adjudicate/latest` | 草稿未变返回最近裁决；草稿已修改返回 409（旧裁决失效） |

裁决响应示例：

```json
{
  "status": "ok",
  "draft_version": 1,
  "primary": {"nodes": ["J1", "J3", "J5", "J6"], "fibers": [4, 5, 6], "length": 12, "attenuation": 4},
  "backup":  {"nodes": ["J1", "J2", "J4", "J6"], "fibers": [1, 2, 3], "length": 13, "attenuation": 4}
}
```

不可行时：`{"status": "infeasible", "message": "无法形成冗余链路：…", "primary": null, "backup": null}`。

## 本地开发（不使用 Docker）

```bash
cd api
pip install -r requirements.txt -r requirements-dev.txt
pytest tests -v                 # 运行测试
uvicorn app.main:app --reload   # 启动 API（http://127.0.0.1:8000）
# 前端为纯静态文件，可用任意静态服务器托管 web/src，
# 并将 /api 反代到 8000 端口（或直接打开页面后把 fetch 地址指向 API）。
```
