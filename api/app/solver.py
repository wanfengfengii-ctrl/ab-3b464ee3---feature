"""冗余双路裁决与检修切换预案算法。

普通裁决（select_redundant_pair）在全部满足约束的双路组合中做穷举
（不允许先贪心定下主路再配套路）：

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

检修切换预案（select_maintenance_plan）同样联合穷举，见函数文档。
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


@dataclass(frozen=True)
class MaintenancePlan:
    """检修切换预案：三条互不相同的线路覆盖检修前与停用期间两个阶段。

    - affected：检修前受影响路，实际使用目标停用光纤；
    - resident：驻留路，检修前与停用期间保持完整接续点与光纤序列不变，
      因此不得使用目标停用光纤；
    - replacement：停用期间替代路，同样不得使用目标停用光纤。

    检修前双路 = (affected, resident)；停用期间双路 = (replacement, resident)。
    两阶段中恰有驻留路不变，另一路在停用开始与恢复后切换。
    """

    resident: Path
    affected: Path
    replacement: Path


def select_maintenance_plan(draft: Draft, fiber_idx: int) -> MaintenancePlan | None:
    """对计划停用的光纤（0 基序号）联合裁决检修切换预案。

    在全部满足约束的 (受影响路, 驻留路, 替代路) 三元组中穷举，而不是
    先裁决普通双路再局部改线（否则可能选中让替代路无解的驻留路，见
    tests/test_solver.py 的联合裁决用例）。约束：

    - 受影响路必须使用目标光纤；驻留路与替代路均不得使用它；
    - 检修前 (受影响路, 驻留路) 与停用期间 (替代路, 驻留路) 各自满足
      中间接续点互不重叠、光纤互不复用（单路衰减上限已在枚举时剪枝）；

    可行三元组按以下关键字依次取最小：
    a. 三条线路中的最长长度；
    b. 三条线路的总衰减；
    c. 替代路相对受影响路（原路）的长度增量；
    d. 受影响路、驻留路、替代路的光纤录入序号序列依次字典序。
    """
    paths = enumerate_paths(draft)
    users = [p for p in paths if fiber_idx in set(p.fibers)]
    avoiders = [p for p in paths if fiber_idx not in set(p.fibers)]
    best: MaintenancePlan | None = None
    best_key: tuple | None = None
    for affected in users:
        a_internal = set(affected.nodes[1:-1])
        a_edges = set(affected.fibers)
        for resident in avoiders:
            if a_edges & set(resident.fibers):
                continue  # 检修前双路复用光纤，排除
            if a_internal & set(resident.nodes[1:-1]):
                continue  # 检修前双路共享中间接续点，排除
            r_internal = set(resident.nodes[1:-1])
            r_edges = set(resident.fibers)
            for replacement in avoiders:
                # 替代路与驻留路必为不同线路：光纤互不复用的检查已涵盖
                if r_edges & set(replacement.fibers):
                    continue  # 停用期间双路复用光纤，排除
                if r_internal & set(replacement.nodes[1:-1]):
                    continue  # 停用期间双路共享中间接续点，排除
                key = (
                    max(affected.length, resident.length, replacement.length),
                    affected.attenuation + resident.attenuation + replacement.attenuation,
                    replacement.length - affected.length,
                    affected.fibers,
                    resident.fibers,
                    replacement.fibers,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best = MaintenancePlan(
                        resident=resident, affected=affected, replacement=replacement,
                    )
    return best
