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
