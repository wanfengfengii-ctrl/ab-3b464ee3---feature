"""裁决算法单元测试。"""

from app.models import Draft, Fiber
from app.solver import enumerate_paths, select_maintenance_plan, select_redundant_pair


def make_draft(nodes, fibers, source, target, limit) -> Draft:
    return Draft(
        nodes=nodes,
        fibers=[Fiber(a=a, b=b, length=l, attenuation=w) for a, b, l, w in fibers],
        source=source,
        target=target,
        attenuation_limit=limit,
    )


SAMPLE_NODES = ["J1", "J2", "J3", "J4", "J5", "J6"]
SAMPLE_FIBERS = [
    ("J1", "J2", 4, 1),  # 1
    ("J2", "J4", 5, 1),  # 2
    ("J4", "J6", 4, 2),  # 3
    ("J1", "J3", 3, 2),  # 4
    ("J3", "J5", 6, 1),  # 5
    ("J5", "J6", 3, 1),  # 6
    ("J2", "J3", 2, 1),  # 7
    ("J4", "J5", 3, 1),  # 8
]


def test_basic_pair_selection():
    draft = make_draft(SAMPLE_NODES, SAMPLE_FIBERS, "J1", "J6", 10)
    result = select_redundant_pair(draft)
    assert result is not None
    primary, backup = result
    # 较短一路为主路
    assert primary.length <= backup.length
    assert primary.nodes == ("J1", "J3", "J5", "J6")
    assert primary.length == 12 and primary.attenuation == 4
    assert backup.nodes == ("J1", "J2", "J4", "J6")
    assert backup.length == 13 and backup.attenuation == 4
    # 中间接续点与光纤均不共享
    assert not (set(primary.nodes[1:-1]) & set(backup.nodes[1:-1]))
    assert not (set(primary.fibers) & set(backup.fibers))


def test_greedy_shortest_primary_would_fail():
    """全局最短路 S-U-V-T 会占住 U、V，使任何备路都不独立；
    正确裁决必须放弃它，选出 (S-U-T, S-V-T)。"""
    draft = make_draft(
        ["S", "T", "U", "V", "M"],
        [
            ("S", "U", 1, 1),   # 1
            ("U", "V", 1, 1),   # 2
            ("V", "T", 1, 1),   # 3
            ("U", "T", 5, 1),   # 4
            ("S", "V", 5, 1),   # 5
            ("S", "M", 10, 1),  # 6
            ("M", "T", 10, 1),  # 7
        ],
        "S", "T", 100,
    )
    result = select_redundant_pair(draft)
    assert result is not None
    primary, backup = result
    assert max(primary.length, backup.length) == 6
    used = {primary.fibers, backup.fibers}
    assert used == {(0, 3), (4, 2)}  # S-U-T 与 S-V-T（0 基序号，按行进方向）


def test_longer_length_minimized_before_attenuation_sum():
    """较长一路长度优先于衰减和：应选 max=9、衰减和 10 的组合，
    而不是 max=10、衰减和 0 的组合。"""
    draft = make_draft(
        ["S", "T", "U", "V", "M"],
        [
            ("S", "U", 1, 0),    # 1
            ("U", "T", 9, 0),    # 2
            ("S", "V", 4, 0),    # 3
            ("V", "T", 5, 0),    # 4
            ("S", "M", 4, 0),    # 5
            ("M", "T", 5, 10),   # 6
            ("U", "T", 100, 0),  # 7
        ],
        "S", "T", 100,
    )
    result = select_redundant_pair(draft)
    assert result is not None
    primary, backup = result
    assert max(primary.length, backup.length) == 9
    assert primary.attenuation + backup.attenuation == 10


def test_attenuation_sum_then_lexicographic_tiebreak():
    """两对候选 max 长度相同：先比衰减和，再比光纤序号序列。"""
    draft = make_draft(
        ["S", "T", "U", "V", "M"],
        [
            ("S", "U", 6, 3),   # 1
            ("U", "T", 4, 2),   # 2
            ("S", "V", 6, 3),   # 3
            ("V", "T", 4, 2),   # 4
            ("S", "M", 4, 1),   # 5
            ("M", "T", 4, 0),   # 6
            ("U", "V", 50, 0),  # 7
        ],
        "S", "T", 100,
    )
    result = select_redundant_pair(draft)
    assert result is not None
    primary, backup = result
    # 候选 (P1,P3) 与 (P2,P3)：max 均 10、衰减和均 6，
    # 主路均为 S-M-T（序号 5,6），备路序号 (1,2) < (3,4)
    assert primary.nodes == ("S", "M", "T")
    assert primary.fibers == (4, 5)
    assert backup.nodes == ("S", "U", "T")
    assert backup.fibers == (0, 1)
    assert primary.attenuation + backup.attenuation == 6


def test_direct_parallel_fibers_form_valid_pair():
    """两条 S-T 直达光纤不共享中间点、不复用光纤，是合法双路。"""
    draft = make_draft(
        ["S", "T", "A", "B", "C"],
        [
            ("S", "T", 3, 1),  # 1
            ("S", "T", 4, 1),  # 2
            ("S", "A", 2, 1),  # 3
            ("A", "T", 2, 1),  # 4
            ("A", "B", 1, 1),  # 5
            ("B", "C", 1, 1),  # 6
            ("C", "A", 1, 1),  # 7
        ],
        "S", "T", 100,
    )
    result = select_redundant_pair(draft)
    assert result is not None
    primary, backup = result
    assert primary.fibers == (0,)   # 直达光纤 1（较短者为主）
    assert backup.fibers == (1,)    # 直达光纤 2


def test_infeasible_when_all_paths_share_articulation_node():
    """所有 S-T 路径都经过接续点 M 时，不存在独立双路。"""
    draft = make_draft(
        ["S", "T", "M", "C", "D"],
        [
            ("S", "M", 1, 1),  # 1
            ("M", "T", 1, 1),  # 2
            ("S", "C", 1, 1),  # 3
            ("C", "D", 1, 1),  # 4
            ("D", "S", 1, 1),  # 5
            ("C", "M", 1, 1),  # 6
            ("D", "M", 1, 1),  # 7
        ],
        "S", "T", 100,
    )
    assert select_redundant_pair(draft) is None


def test_attenuation_limit_filters_paths():
    draft = make_draft(SAMPLE_NODES, SAMPLE_FIBERS, "J1", "J6", 10)
    assert select_redundant_pair(draft) is not None
    # 上限收紧到 3 后，所有路径衰减均 >= 4，无可行双路
    strict = make_draft(SAMPLE_NODES, SAMPLE_FIBERS, "J1", "J6", 3)
    assert enumerate_paths(strict) == []
    assert select_redundant_pair(strict) is None


def test_each_path_individually_within_limit():
    """双路各自的衰减都必须不超过上限。"""
    draft = make_draft(
        ["S", "T", "U", "V", "W"],
        [
            ("S", "U", 1, 4),  # 1
            ("U", "T", 1, 4),  # 2  -> S-U-T 衰减 8
            ("S", "V", 1, 1),  # 3
            ("V", "T", 1, 1),  # 4  -> S-V-T 衰减 2
            ("S", "W", 1, 1),  # 5
            ("W", "T", 1, 1),  # 6  -> S-W-T 衰减 2
            ("V", "W", 1, 1),  # 7
        ],
        "S", "T", 5,
    )
    result = select_redundant_pair(draft)
    assert result is not None
    primary, backup = result
    assert primary.attenuation <= 5 and backup.attenuation <= 5
    assert 8 not in (primary.attenuation, backup.attenuation)


# ---------------- 检修切换预案 ----------------


def three_corridor_draft() -> Draft:
    """三条互不共享中间接续点的走廊；走廊 A 首段为计划停用光纤 #1。"""
    return make_draft(
        ["J1", "J2", "J3", "J4", "J5", "J6", "J7", "J8"],
        [
            ("J1", "J2", 3, 1),  # 1  ← 计划停用
            ("J2", "J3", 3, 1),  # 2
            ("J3", "J8", 3, 1),  # 3   走廊 A：J1-J2-J3-J8，长 9
            ("J1", "J4", 4, 1),  # 4
            ("J4", "J5", 4, 1),  # 5
            ("J5", "J8", 4, 1),  # 6   走廊 B：J1-J4-J5-J8，长 12
            ("J1", "J6", 4, 1),  # 7
            ("J6", "J7", 4, 1),  # 8
            ("J7", "J8", 4, 1),  # 9   走廊 C：J1-J6-J7-J8，长 12
        ],
        "J1", "J8", 10,
    )


def assert_maintenance_invariants(plan, target_idx, limit):
    """预案通用不变量：两阶段各自独立、目标光纤使用符合阶段、驻留路不变。"""
    affected, resident, replacement = plan.affected, plan.resident, plan.replacement
    # 检修前双路实际使用目标光纤；停用期间双路不使用
    assert target_idx in set(affected.fibers)
    assert target_idx not in set(resident.fibers)
    assert target_idx not in set(replacement.fibers)
    # 三条线路互不相同
    assert len({affected, resident, replacement}) == 3
    # 两阶段各自的中间接续点独立、光纤不复用
    for x, y in ((affected, resident), (replacement, resident)):
        assert not (set(x.nodes[1:-1]) & set(y.nodes[1:-1]))
        assert not (set(x.fibers) & set(y.fibers))
    # 任一阶段每路衰减不超限
    for p in (affected, resident, replacement):
        assert p.attenuation <= limit


def test_maintenance_basic_three_corridors():
    draft = three_corridor_draft()
    plan = select_maintenance_plan(draft, 0)  # 停用光纤 #1
    assert plan is not None
    assert_maintenance_invariants(plan, 0, 10)
    # 唯一使用 #1 的线路是走廊 A，即受影响路
    assert plan.affected.fibers == (0, 1, 2)
    assert plan.affected.nodes == ("J1", "J2", "J3", "J8")
    # 走廊 B、C 长度相同：驻留/替代两种指派的三路最长长度、总衰减、
    # 长度增量均并列，由序号序列裁决 —— 驻留路取序号序列较小者
    assert plan.resident.fibers == (3, 4, 5)
    assert plan.replacement.fibers == (6, 7, 8)


def test_maintenance_joint_selection_not_greedy():
    """先裁决普通双路再局部改线会失败：使用 #1 的最优普通双路是
    (S-U-T, S-V1-V2-T)，但 S-V1-V2-T 与任何不用 #1 的线路都共享接续点，
    找不到替代路。联合裁决必须放弃它，改选 (S-U-T, S-X-V2-T, S-V1-W-T)。"""
    draft = make_draft(
        ["S", "T", "U", "V1", "V2", "W", "X"],
        [
            ("S", "U", 1, 0),    # 1  ← 计划停用
            ("U", "T", 1, 0),    # 2   S-U-T 长 2
            ("S", "V1", 2, 0),   # 3
            ("V1", "V2", 2, 0),  # 4
            ("V2", "T", 2, 0),   # 5   S-V1-V2-T 长 6
            ("V1", "W", 3, 0),   # 6
            ("W", "T", 3, 0),    # 7   S-V1-W-T 长 8
            ("S", "X", 3, 0),    # 8
            ("X", "V2", 4, 0),   # 9   S-X-V2-T 长 9
        ],
        "S", "T", 10,
    )

    def disjoint(p, q):
        return not (set(p.nodes[1:-1]) & set(q.nodes[1:-1])) and not (
            set(p.fibers) & set(q.fibers)
        )

    # 贪心基线：先取使用 #1 的最优普通双路，其另一路为 S-V1-V2-T
    paths = enumerate_paths(draft)
    users = [p for p in paths if 0 in set(p.fibers)]
    avoiders = [p for p in paths if 0 not in set(p.fibers)]
    greedy_pair = min(
        ((max(a.length, r.length), a.attenuation + r.attenuation, a, r)
         for a in users for r in avoiders if disjoint(a, r)),
        key=lambda t: (t[0], t[1]),
    )
    greedy_resident = greedy_pair[3]
    assert greedy_resident.nodes == ("S", "V1", "V2", "T")
    # 对该驻留路不存在任何不用 #1 的独立替代路 —— 贪心改线无解
    assert not any(disjoint(greedy_resident, b) for b in avoiders)

    # 联合裁决仍能找到预案
    plan = select_maintenance_plan(draft, 0)
    assert plan is not None
    assert_maintenance_invariants(plan, 0, 10)
    assert plan.affected.nodes == ("S", "U", "T")
    assert plan.affected.fibers == (0, 1)
    # 两种可行指派的三路最长长度（9）与总衰减（0）并列，
    # 由长度增量裁决：替代路 S-V1-W-T（增量 8-2=6）优于 S-X-V2-T（9-2=7）
    assert plan.resident.nodes == ("S", "X", "V2", "T")
    assert plan.resident.fibers == (7, 8, 4)
    assert plan.replacement.nodes == ("S", "V1", "W", "T")
    assert plan.replacement.fibers == (2, 5, 6)


def test_maintenance_max_of_three_lengths_decides_first():
    """三路最长长度优先于总衰减：应选 max=10、衰减和 50 的组合，
    而不是 max=12、衰减和 0 的组合。"""
    draft = make_draft(
        ["S", "T", "U", "V", "W", "X"],
        [
            ("S", "U", 2, 0),   # 1  ← 计划停用
            ("U", "T", 2, 0),   # 2   A = S-U-T 长 4 衰减 0
            ("S", "V", 5, 25),  # 3
            ("V", "T", 5, 25),  # 4   P1 长 10 衰减 50
            ("S", "W", 4, 0),   # 5
            ("W", "T", 4, 0),   # 6   P2 长 8 衰减 0
            ("S", "X", 6, 0),   # 7
            ("X", "T", 6, 0),   # 8   P3 长 12 衰减 0
        ],
        "S", "T", 100,
    )
    plan = select_maintenance_plan(draft, 0)
    assert plan is not None
    assert_maintenance_invariants(plan, 0, 100)
    # 含 P3 的三元组最长长度为 12，被排除；驻留/替代来自 P1、P2
    assert set(plan.resident.fibers) | set(plan.replacement.fibers) == {2, 3, 4, 5}
    # 长度增量：替代路 P2（8-4=4）优于 P1（10-4=6），故驻留路为 P1
    assert plan.resident.fibers == (2, 3)
    assert plan.replacement.fibers == (4, 5)


def test_maintenance_total_attenuation_decides_second():
    """三路最长长度并列时，总衰减小者胜出（P1 衰减 10 的组合被排除）。"""
    draft = make_draft(
        ["S", "T", "U", "V", "W", "X"],
        [
            ("S", "U", 2, 0),  # 1  ← 计划停用
            ("U", "T", 2, 0),  # 2   A = S-U-T 长 4 衰减 0
            ("S", "V", 3, 5),  # 3
            ("V", "T", 3, 5),  # 4   P1 长 6 衰减 10
            ("S", "W", 3, 0),  # 5
            ("W", "T", 3, 0),  # 6   P2 长 6 衰减 0
            ("S", "X", 3, 1),  # 7
            ("X", "T", 3, 1),  # 8   P3 长 6 衰减 2
        ],
        "S", "T", 100,
    )
    plan = select_maintenance_plan(draft, 0)
    assert plan is not None
    assert_maintenance_invariants(plan, 0, 100)
    # {P2, P3} 总衰减 2 < {P1, P2} 的 10；指派并列时驻留路取序号序列较小者
    assert plan.resident.fibers == (4, 5)
    assert plan.replacement.fibers == (6, 7)


def test_maintenance_infeasible_when_fiber_is_bridge():
    """所有路径都经过光纤 #2（M-T）时，停用期间无任何可用线路。"""
    draft = make_draft(
        ["S", "T", "M", "C", "D"],
        [
            ("S", "M", 1, 1),  # 1
            ("M", "T", 1, 1),  # 2  ← 计划停用（桥）
            ("S", "C", 1, 1),  # 3
            ("C", "D", 1, 1),  # 4
            ("D", "S", 1, 1),  # 5
            ("C", "M", 1, 1),  # 6
            ("D", "M", 1, 1),  # 7
        ],
        "S", "T", 100,
    )
    assert select_maintenance_plan(draft, 1) is None


def test_maintenance_infeasible_when_only_one_route_avoids_fiber():
    """样例拓扑中光纤 #5 位于短走廊：使用它的线路会占住 J3、J5，
    剩余不用 #5 的线路不足以组成停用期间双路。"""
    draft = make_draft(SAMPLE_NODES, SAMPLE_FIBERS, "J1", "J6", 10)
    assert select_redundant_pair(draft) is not None  # 普通双路存在
    assert select_maintenance_plan(draft, 4) is None  # 但无法只切换一路覆盖停纤


def test_maintenance_respects_attenuation_limit():
    """停用期间的替代路衰减超限时，该预案不可行。"""
    corridor = three_corridor_draft()
    assert select_maintenance_plan(corridor, 0) is not None
    # 上限收紧到 2：每条走廊衰减为 3，所有路径被剪枝
    strict = make_draft(
        corridor.nodes,
        [(f.a, f.b, f.length, f.attenuation) for f in corridor.fibers],
        corridor.source, corridor.target, 2,
    )
    assert select_maintenance_plan(strict, 0) is None

