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
- 可从已录入光纤中选择一段**计划检修停用的光纤**，生成**检修切换预案**：检修前与
  停用期间均保持双路独立，且两阶段中恰有一路（驻留路）保持完整接续点与光纤序列
  不变，仅另一路在停用开始与恢复后切换；
- 无法在只切换一路的条件下覆盖计划停纤时，页面明确显示**无法形成无中断检修预案**；
- 修改任一草稿字段（含目标停用光纤）后，旧裁决与旧检修预案立即失效
  （前端本地作废 + 服务端版本校验双重保证）。

## 裁决规则（服务端穷举，非贪心）

服务端枚举主控室到展柜的全部衰减不超限的简单路径，再遍历所有满足
「中间接续点互不重叠、光纤互不复用」的路径对，按以下关键字**依次**取最小：

1. **较长一路的总长度**最小；
2. **两路衰减之和**最小；
3. 主路的光纤录入序号序列（按行进方向）字典序最小，再比较备路的序号序列。

其中主路 = 较短一路；长度相同取衰减较小者；再相同取序号序列字典序较小者。
算法不会先贪心定下主路再配套路（例如全局最短路会占住所有备用接续点时，
系统会放弃它而选择整体更优的双路组合，见 `api/tests/test_solver.py`）。

## 检修切换预案裁决规则（联合裁决，非先裁决再改线）

针对草稿中选择的计划停用光纤，服务端**联合选择**三条互不相同的线路：

- **受影响路**：检修前使用，实际经过目标停用光纤；
- **驻留路**：检修前与停用期间均使用，完整接续点与光纤序列不变，
  因此不经过目标停用光纤；
- **替代路**：停用期间顶替受影响路，同样不经过目标停用光纤。

检修前双路 = 受影响路 + 驻留路；停用期间双路 = 替代路 + 驻留路。任一阶段
仍须满足接续点独立、光纤不复用与衰减上限。算法穷举全部满足约束的
（受影响路、驻留路、替代路）三元组——**不会**先裁决普通双路再局部改线
（否则可能选中让替代路无解的驻留路），按以下关键字**依次**取最小：

1. 三条线路中的**最长长度**；
2. 三条线路的**总衰减**；
3. **替代路相对受影响路（原路）的长度增量**；
4. 受影响路、驻留路、替代路的光纤录入序号序列依次字典序。

不存在可行三元组时返回 `infeasible`，页面明确提示**无法形成无中断检修预案**。

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

1. **代码测试**：`pytest` 运行 `api/tests`（算法、约束校验、裁决失效、检修预案等）；
2. **构建校验**：编译后端源码并导入应用；
3. **API 冒烟**：对 compose 网络中真实运行的服务发起请求——健康检查、
   经 nginx 代理的完整裁决流程、草稿修改后旧裁决失效（409）、不可行场景
   必须返回「无法形成冗余链路」、检修预案的联合裁决/失效/不可行报告、
   录入约束 422 校验等。

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
| POST | `/api/maintenance/plan` | 对草稿中的计划停用光纤联合裁决检修切换预案 |
| GET | `/api/maintenance/plan/latest` | 草稿未变返回最近预案；草稿或目标停用光纤已修改返回 409 |

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

检修切换预案响应示例（草稿字段 `maintenance_fiber` 为 1 基录入序号）：

```json
{
  "status": "ok",
  "draft_version": 2,
  "maintenance_fiber": 1,
  "affected":     {"nodes": ["J1", "J2", "J3", "J8"], "fibers": [1, 2, 3], "length": 9,  "attenuation": 3},
  "resident":     {"nodes": ["J1", "J4", "J5", "J8"], "fibers": [4, 5, 6], "length": 12, "attenuation": 3},
  "replacement":  {"nodes": ["J1", "J6", "J7", "J8"], "fibers": [7, 8, 9], "length": 12, "attenuation": 3}
}
```

无法覆盖停纤时：`{"status": "infeasible", "message": "无法形成无中断检修预案：…",
"resident": null, "affected": null, "replacement": null}`。

## 本地开发（不使用 Docker）

```bash
cd api
pip install -r requirements.txt -r requirements-dev.txt
pytest tests -v                 # 运行测试
uvicorn app.main:app --reload   # 启动 API（http://127.0.0.1:8000）
# 前端为纯静态文件，可用任意静态服务器托管 web/src，
# 并将 /api 反代到 8000 端口（或直接打开页面后把 fetch 地址指向 API）。
```
