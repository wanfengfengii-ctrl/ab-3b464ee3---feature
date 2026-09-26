"""冗余双路裁决算法。

在全部满足约束的双路组合中做穷举裁决（不允许先贪心定下主路再配套路）：

1. 枚举主控室到展柜的全部简单路径，且单路衰减不超过上限；
2. 遍历所有无序路径对，要求：
   - 除两端（主控室、展柜）外不共享任何接续点；
   - 不复用同一段光纤（注意：两条都走 S-T 直达光纤时虽不共享中间点，
     仍因复用光纤而被排除）；
3. 可行路径对按以下关键字依次比较，取最小者：
   a. 较长一路的总长度最小；
   b. 两路衰减之和最小；
   c. 主路光纤录入序号序列（按行进方向）字典序最小，再比较备路序列。
   其中主路 = 较短一路；长度相同取衰减较小者；再相同取序号序列字典序较小者。

此外提供计划检修的「单路切换」预案裁决（plan_maintenance_switch）：
检修前双路（affected、resident）必须实际经过停用光纤 e*；停用期间
受影响路切换为不经过 e* 的替代路（alternate），驻留路两阶段完全不变。
三路（三种角色）必须两两不同，联合穷举选出，不允许先裁决普通双路再局部改线。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Draft


@dataclass(frozen=True)
class Path:
    nodes: tuple[str, ...]      # 途经接续点，自主控室至展柜
    fibers: tuple[int, ...]     # 使用光纤的录入序号（0 基，按行进方向）
    length: int                 # 总长度
    attenuation: int            # 总衰减


@dataclass(frozen=True)
class MaintenancePlan:
    """单路切换检修预案。

    before_affected / before_resident：检修前双路（before_affected 实际经过
    停用光纤）；alternate：停用期间替代路（不经过停用光纤）。
    switched_from / switched_to：受影响路在停用开始（from → alternate）与
    恢复后（alternate → from）两次切换的两端，即 before_affected 与 alternate。
    """

    before_affected: Path
    before_resident: Path
    alternate: Path
    outage_fiber: int           # 停用光纤的 0 基录入序号


def enumerate_paths(draft: Draft) -> list[Path]:
    """DFS 枚举全部衰减不超限的简单路径。"""
    adj: dict[str, list[tuple[str, int]]] = {n: [] for n in draft.nodes}
    for idx, f in enumerate(draft.fibers):
        adj[f.a].append((f.b, idx))
        adj[f.b].append((f.a, idx))

    fibers = draft.fibers
    limit = draft.attenuation_limit
    paths: list[Path] = []

    def dfs(cur: str, visited: set[str], used: set[int],
            ns: list[str], fs: list[int], length: int, att: int) -> None:
        if cur == draft.target:
            paths.append(Path(tuple(ns), tuple(fs), length, att))
            return
        for nxt, ei in adj[cur]:
            if nxt in visited or ei in used:
                continue
            na = att + fibers[ei].attenuation
            if na > limit:
                continue  # 单路衰减超限，剪枝
            visited.add(nxt)
            used.add(ei)
            ns.append(nxt)
            fs.append(ei)
            dfs(nxt, visited, used, ns, fs, length + fibers[ei].length, na)
            ns.pop()
            fs.pop()
            visited.discard(nxt)
            used.discard(ei)

    dfs(draft.source, {draft.source}, set(), [draft.source], [], 0, 0)
    return paths


def _order_pair(p: Path, q: Path) -> tuple[Path, Path]:
    """确定主备：较短者为主路；并列时衰减小者为主；再并列时序号序列小者为主。"""
    key_p = (p.length, p.attenuation, p.fibers)
    key_q = (q.length, q.attenuation, q.fibers)
    return (p, q) if key_p <= key_q else (q, p)


def select_redundant_pair(draft: Draft) -> tuple[Path, Path] | None:
    """返回 (主路, 备路)；不存在满足约束的双路时返回 None。"""
    paths = enumerate_paths(draft)
    best: tuple[Path, Path] | None = None
    best_key: tuple | None = None
    for i in range(len(paths)):
        p = paths[i]
        p_internal = set(p.nodes[1:-1])
        p_edges = set(p.fibers)
        for j in range(i + 1, len(paths)):
            q = paths[j]
            if p_edges & set(q.fibers):
                continue  # 复用同一光纤，排除
            if p_internal & set(q.nodes[1:-1]):
                continue  # 共享中间接续点，排除
            primary, backup = _order_pair(p, q)
            key = (
                max(p.length, q.length),            # 较长一路长度最小
                p.attenuation + q.attenuation,      # 两路衰减和最小
                primary.fibers,                     # 主路序号序列字典序
                backup.fibers,                      # 备路序号序列字典序
            )
            if best_key is None or key < best_key:
                best_key = key
                best = (primary, backup)
    return best


def _compatible(p: Path, q: Path) -> bool:
    """两路是否构成合法双路：中间接续点互不重叠、光纤互不复用。"""
    return not (set(p.fibers) & set(q.fibers)) and not (
        set(p.nodes[1:-1]) & set(q.nodes[1:-1])
    )


def plan_maintenance_switch(
    draft: Draft, outage_fiber: int
) -> MaintenancePlan | None:
    """联合裁决计划检修的单路切换预案；无法只切换一路覆盖停纤时返回 None。

    穷举三种不同角色的线路（联合选择，不先裁决普通双路再局部改线）：
    - affected（检修前受影响路）：必须实际经过停用光纤 e*；
    - resident（驻留路）：检修前与停用期间完全不变（接续点与光纤序列一致），
      不得经过 e*（否则停用期间也会中断，不成其为驻留路）；
    - alternate（停用期间替代路）：不得经过 e*；
    任一阶段 (affected, resident) 与 (alternate, resident) 都必须满足
    接续点独立、光纤不复用，且三路各自衰减不超限（enumerate_paths 已保证）。

    裁决键依次取最小：
    1. 三条线路中的最长长度 max(len(A), len(R), len(Q))；
    2. 三条线路总衰减之和；
    3. 替代路相对原路（受影响路）的长度增量 len(Q) - len(A)；
    4. 录入序号序列稳定裁决：affected、resident、alternate 的序号序列依次字典序。
    """
    paths = enumerate_paths(draft)
    using = [p for p in paths if outage_fiber in p.fibers]
    # 停用期间与驻留路都不得使用停用光纤
    avoiding = [p for p in paths if outage_fiber not in p.fibers]

    best: MaintenancePlan | None = None
    best_key: tuple | None = None
    for affected in using:
        for resident in avoiding:
            if not _compatible(affected, resident):
                continue  # 检修前双路即不合法
            r_nodes = set(resident.nodes[1:-1])
            r_edges = set(resident.fibers)
            for alternate in avoiding:
                # 恰有一路（驻留路）保持不变：另外两路必须是不同的线路，
                # 否则检修前与停用期间两路均未改变，等于没有切换；
                # 三种角色因此必须对应三条不同的线路。
                if alternate == affected or alternate == resident:
                    continue
                q_edges = set(alternate.fibers)
                # 停用期间 (alternate, resident) 仍须满足双路独立约束。
                if q_edges & r_edges:
                    continue  # 复用光纤
                if set(alternate.nodes[1:-1]) & r_nodes:
                    continue  # 共享中间接续点
                # 替代路与原受影响路之间复用 e* 以外的光纤、或经过相同中间点均允许：
                # 二者从不同时承载业务（受影响路停用开始即退出、恢复后替代路退出）。
                key = (
                    max(affected.length, resident.length, alternate.length),
                    affected.attenuation
                    + resident.attenuation
                    + alternate.attenuation,
                    alternate.length - affected.length,
                    affected.fibers,
                    resident.fibers,
                    alternate.fibers,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best = MaintenancePlan(
                        before_affected=affected,
                        before_resident=resident,
                        alternate=alternate,
                        outage_fiber=outage_fiber,
                    )
    return best
