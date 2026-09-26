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

此外，可从已录入光纤中选择一段**计划检修停用的光纤**，生成**只切换一路**的检修预案：

- **检修前**双路必须**实际使用**该停用光纤（由受影响路承载），**停用期间**两路均
  **不得使用**它；
- 两阶段中**恰有一路保持不变**（驻留路：完整接续点与光纤序列一致），另一路在
  停用开始时切到替代路、恢复后切回；
- 任一阶段仍满足接续点独立、光纤不复用与每路衰减上限；
- 服务端**联合穷举**受影响路、驻留路、替代路三条线路，不先裁决普通双路再局部改线；
- 无法在只切换一路的条件下覆盖计划停纤时，页面明确显示**无法形成无中断检修预案**；
- 草稿或目标停用光纤改变后，旧预案立即失效。

## 裁决规则（服务端穷举，非贪心）

服务端枚举主控室到展柜的全部衰减不超限的简单路径，再遍历所有满足
「中间接续点互不重叠、光纤互不复用」的路径对，按以下关键字**依次**取最小：

1. **较长一路的总长度**最小；
2. **两路衰减之和**最小；
3. 主路的光纤录入序号序列（按行进方向）字典序最小，再比较备路的序号序列。

其中主路 = 较短一路；长度相同取衰减较小者；再相同取序号序列字典序较小者。
算法不会先贪心定下主路再配套路（例如全局最短路会占住所有备用接续点时，
系统会放弃它而选择整体更优的双路组合，见 `api/tests/test_solver.py`）。

## 检修切换预案裁决规则（服务端三路联合穷举）

选定计划停用光纤 e\* 后，服务端在**同一拓扑**中联合枚举三种不同角色的线路：
受影响路 A（检修前承载）、驻留路 R（两阶段不变）、替代路 Q（停用期间承载），
而**不是**先对草稿做普通双路裁决再局部改线：

- A 必须实际经过 e\*；R 与 Q 都不得经过 e\*；
- 检修前 (A, R) 与停用期间 (Q, R) 各自满足中间接续点互不重叠、光纤互不复用；
- R 在两阶段的接续点与光纤序列完全一致；A、R、Q 是三条不同的线路
  （恰有一路保持不变、另一路在停用开始与恢复后切换）；
- 三条线路各自衰减不超过上限（路径枚举阶段即剪枝）。

候选三元组按以下关键字**依次**取最小：

1. **三条线路中的最长长度** `max(len(A), len(R), len(Q))`；
2. **三条线路的总衰减之和**；
3. **替代路相对原路（受影响路）的长度增量** `len(Q) - len(A)`；
4. **录入序号序列稳定裁决**：A、R、Q 的光纤序号序列（按行进方向）依次字典序。

注意它与普通裁决的关键次序差异：普通裁决比较两路衰减之和，检修预案比较
三路衰减之和，且第三键为替代路的长度增量。不存在可行三元组时
（例如去掉 e\* 后所有路径都汇聚于同一接续点），返回
`无法形成无中断检修预案`，见 `api/tests/test_maintenance.py`。

## 目录结构

```
├── docker-compose.yml      # api / web / verify 三个服务
├── api/                    # FastAPI 后端（裁决算法、检修预案、草稿与裁决状态）
│   ├── app/{main,models,solver}.py
│   ├── tests/              # pytest 单元与 API 测试（含检修预案）
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
   必须返回「无法形成冗余链路」、录入约束 422 校验，以及检修切换预案的
   完整流程（分阶段双路独立、停用光纤/草稿改变后旧预案失效 409、无法只切换
   一路时必须返回「无法形成无中断检修预案」）等。

完成后 `verify` 自行退出，退出码即验证结果。

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/constraints` | 录入约束（5–9 接续点、7–15 光纤） |
| PUT | `/api/draft` | 保存草稿；任何字段变化使旧裁决与旧检修预案失效（版本号 +1） |
| GET | `/api/draft` | 查看当前草稿 |
| POST | `/api/adjudicate` | 对当前草稿裁决，返回主备双路或 `infeasible` |
| GET | `/api/adjudicate/latest` | 草稿未变返回最近裁决；草稿已修改返回 409（旧裁决失效） |
| PUT | `/api/maintenance/outage` | 登记计划停用光纤（1 基录入序号）；改选后旧预案失效 |
| POST | `/api/maintenance/plan` | 联合裁决检修切换预案，返回分阶段两组线路或 `infeasible` |
| GET | `/api/maintenance/latest` | 草稿/停用光纤未变返回最近预案；任一改变返回 409 |

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

检修切换预案响应（`POST /api/maintenance/plan`，`{"outage_fiber": 5}`）示例：

```json
{
  "status": "ok",
  "draft_version": 3,
  "outage_fiber": 5,
  "before":   {"affected": {"nodes": ["J1", "J2", "J6"], "fibers": [5, 7], "length": 10, "attenuation": 3},
               "resident": {"nodes": ["J1", "J3", "J6"], "fibers": [1, 2], "length": 11, "attenuation": 3}},
  "during":   {"alternate": {"nodes": ["J1", "J4", "J6"], "fibers": [8, 9], "length": 12, "attenuation": 3},
               "resident": {"nodes": ["J1", "J3", "J6"], "fibers": [1, 2], "length": 11, "attenuation": 3}},
  "affected":  { "...同上 before.affected..." },
  "resident":  { "...同上 before.resident（与 during.resident 完全相同）..." },
  "alternate": { "...同上 during.alternate..." },
  "switched_path": {"from": { "...affected..." }, "to": { "...alternate..." }, "length_delta": 2},
  "message": null
}
```

`before` 与 `during` 在同一拓扑中分阶段给出两组线路；`resident` 为驻留路
（两阶段不变），`switched_path` 为停用开始/恢复后的切换线路及其长度增量。
不可行时：`{"status": "infeasible", "message": "无法形成无中断检修预案：…",
"before": null, "during": null, "affected": null, "resident": null,
"alternate": null, "switched_path": null}`。

## 本地开发（不使用 Docker）

```bash
cd api
pip install -r requirements.txt -r requirements-dev.txt
pytest tests -v                 # 运行测试
uvicorn app.main:app --reload   # 启动 API（http://127.0.0.1:8000）
# 前端为纯静态文件，可用任意静态服务器托管 web/src，
# 并将 /api 反代到 8000 端口（或直接打开页面后把 fetch 地址指向 API）。
```
