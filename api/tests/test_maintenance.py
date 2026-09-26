"""计划检修单路切换预案的裁决算法测试。"""

from app.models import Draft, Fiber
from app.solver import enumerate_paths, plan_maintenance_switch, select_redundant_pair


def make_draft(nodes, fibers, source, target, limit) -> Draft:
    return Draft(
        nodes=nodes,
        fibers=[Fiber(a=a, b=b, length=l, attenuation=w) for a, b, l, w in fibers],
        source=source,
        target=target,
        attenuation_limit=limit,
    )


def assert_valid_plan(plan, outage_idx, limit):
    """通用不变量：检修前/停用期间均为合法双路，且只切换一路。"""
    a, r, q = plan.before_affected, plan.before_resident, plan.alternate

    # 检修前双路必须实际使用停用光纤（受影响路承载它）
    assert outage_idx in a.fibers
    # 停用期间任何在用线路都不得使用停用光纤
    assert outage_idx not in r.fibers
    assert outage_idx not in q.fibers

    # 三条角色线路两两不同
    assert len({a.fibers, r.fibers, q.fibers}) == 3

    # 检修前：接续点独立、光纤不复用
    assert not (set(a.fibers) & set(r.fibers))
    assert not (set(a.nodes[1:-1]) & set(r.nodes[1:-1]))
    # 停用期间：接续点独立、光纤不复用
    assert not (set(q.fibers) & set(r.fibers))
    assert not (set(q.nodes[1:-1]) & set(r.nodes[1:-1]))

    # 驻留路两阶段完整接续点与光纤序列不变
    assert plan.before_resident == r

    # 任一阶段各线路衰减均不超限
    assert a.attenuation <= limit and r.attenuation <= limit and q.attenuation <= limit


# 三条独立走廊：A=S-A-T（含停用光纤 #1）、B=S-B-…-T、C=S-C-T
MAINT_NODES = ["S", "T", "A", "B", "C", "D"]
MAINT_FIBERS = [
    ("S", "A", 1, 1),    # 1（计划停用）
    ("A", "T", 1, 1),    # 2
    ("S", "B", 2, 1),    # 3
    ("B", "C", 2, 1),    # 4
    ("S", "C", 4, 1),    # 5
    ("C", "T", 4, 1),    # 6
    ("C", "T", 2, 1),    # 7
    ("B", "D", 2, 1),    # 8
    ("D", "T", 2, 1),    # 9
]


def test_basic_maintenance_plan():
    draft = make_draft(MAINT_NODES, MAINT_FIBERS, "S", "T", 1000)
    plan = plan_maintenance_switch(draft, 0)
    assert plan is not None
    assert_valid_plan(plan, 0, 1000)
    # 受影响路为 S-A-T；驻留/替代在 S-B-D-T（#3,#8,#9，长 6）与
    # S-C-T（#5,#7，长 6）间按序号序列裁决：驻留路取序号较小者。
    assert plan.before_affected.fibers == (0, 1)
    assert plan.before_resident.fibers == (2, 7, 8)
    assert plan.alternate.fibers == (4, 6)
    assert plan.alternate.length - plan.before_affected.length == 4
    assert max(
        plan.before_affected.length,
        plan.before_resident.length,
        plan.alternate.length,
    ) == 6


def test_maintenance_is_joint_selection_not_local_reroute():
    """服务端必须联合选择三路，不能先裁决普通双路再局部改线。

    普通最优双路是两条短走廊 S-P-T / S-Q-T，它们完全不碰计划停用光纤 #5；
    若先裁决这对双路再局部改线，无论怎么改都不可能满足「检修前双路必须
    实际使用停用光纤」。联合裁决把含停用光纤的 S-U-T 选为检修前受影响路，
    再从两条短走廊中分配驻留路与替代路。
    """
    draft = make_draft(
        ["S", "T", "P", "Qn", "U"],
        [
            ("S", "P", 1, 1),    # 1
            ("P", "T", 1, 1),    # 2  -> 短走廊 P：长 2
            ("S", "Qn", 1, 1),   # 3
            ("Qn", "T", 1, 1),   # 4  -> 短走廊 Q：长 2
            ("S", "U", 100, 1),  # 5（计划停用）
            ("U", "T", 100, 1),  # 6  -> 含停纤走廊 U：长 200
            ("P", "Qn", 50, 1),  # 7
        ],
        "S", "T", 1000,
    )
    normal = select_redundant_pair(draft)
    assert normal is not None
    assert {normal[0].fibers, normal[1].fibers} == {(0, 1), (2, 3)}
    # 普通裁决的双路完全不使用停用光纤
    assert 4 not in normal[0].fibers and 4 not in normal[1].fibers

    plan = plan_maintenance_switch(draft, 4)
    assert plan is not None
    assert_valid_plan(plan, 4, 1000)
    # 检修前受影响路必须承载停用光纤（普通双路里没有任何一路能充当它）
    assert plan.before_affected.fibers == (4, 5)
    # 两条短走廊按序号序列分配：驻留路 #1,#2、替代路 #3,#4
    assert plan.before_resident.fibers == (0, 1)
    assert plan.alternate.fibers == (2, 3)


def test_max_length_takes_priority_over_attenuation():
    """裁决键 1（三路最长长度）优先于裁决键 2（总衰减和）：

    避开 #1 的两条短走廊（B1、C1，长 3、衰减各 10）组合的最长长度为 3，
    但衰减和高达 20；另有零衰减长走廊（长 8）。必须选短而高衰减的组合。
    """
    draft = make_draft(
        ["S", "T", "A", "B1", "C1", "D1"],
        [
            ("S", "A", 1, 0),    # 1 停用
            ("A", "T", 1, 0),    # 2  -> 受影响路长 2、衰减 0
            ("S", "B1", 1, 5),   # 3
            ("B1", "T", 2, 5),   # 4  -> 短走廊 B1：长 3、衰减 10
            ("S", "D1", 4, 0),   # 5
            ("D1", "T", 4, 0),   # 6  -> 长走廊 D1：长 8、衰减 0
            ("S", "C1", 1, 5),   # 7
            ("C1", "T", 2, 5),   # 8  -> 短走廊 C1：长 3、衰减 10
        ],
        "S", "T", 1000,
    )
    plan = plan_maintenance_switch(draft, 0)
    assert plan is not None
    assert_valid_plan(plan, 0, 1000)
    assert plan.before_resident.fibers == (2, 3)
    assert plan.alternate.fibers == (6, 7)
    longest = max(
        plan.before_affected.length,
        plan.before_resident.length,
        plan.alternate.length,
    )
    att_sum = sum(
        x.attenuation
        for x in (plan.before_affected, plan.before_resident, plan.alternate)
    )
    assert longest == 3
    # 明知衰减和更差仍因最长长度优先而选中
    assert att_sum == 20


def test_length_delta_tiebreak_before_sequences():
    """最长长度与总衰减并列时，先比替代路相对原路的长度增量。"""
    fibers = [
        ("S", "A", 2, 1),    # 1 停用
        ("A", "T", 2, 1),    # 2  -> A 路长 4
        ("S", "B", 3, 1),    # 3
        ("B", "C", 3, 1),    # 4
        ("S", "C", 4, 1),    # 5
        ("C", "T", 4, 1),    # 6  -> C 路长 8
        ("C", "T", 50, 1),   # 7
        ("B", "D", 3, 1),    # 8
        ("D", "T", 3, 1),    # 9  -> B-D 路长 9，B-C(#7) 路长 56
    ]
    draft = make_draft(MAINT_NODES, fibers, "S", "T", 1000)
    plan = plan_maintenance_switch(draft, 0)
    assert plan is not None
    assert_valid_plan(plan, 0, 1000)
    # 候选：R=C 路(#5,#6,8) + Q=B-D(9)：最长 9、增量 9-4=5
    #       R=B-D(9) + Q=C 路(8)：最长 9、增量 8-4=4 → 增量更小者胜
    assert plan.before_resident.fibers == (2, 7, 8)
    assert plan.alternate.fibers == (4, 5)


def test_sequence_stable_tiebreak():
    """前三个裁决键全部并列时，按 affected、resident、alternate 序号序列稳定裁决。

    三条 S-T 直达光纤并列：停用 #1，驻留路必取序号最小的 #2，替代路取 #3。
    """
    draft = make_draft(
        ["S", "T", "X", "Y", "Z"],
        [
            ("S", "T", 1, 1),  # 1 停用
            ("S", "T", 1, 1),  # 2
            ("S", "T", 1, 1),  # 3
            ("S", "X", 1, 1),  # 4
            ("X", "Y", 1, 1),  # 5
            ("Y", "Z", 1, 1),  # 6
            ("Z", "S", 1, 1),  # 7
        ],
        "S", "T", 1000,
    )
    plan = plan_maintenance_switch(draft, 0)
    assert plan is not None
    assert plan.before_affected.fibers == (0,)
    assert plan.before_resident.fibers == (1,)
    assert plan.alternate.fibers == (2,)


def test_maintenance_infeasible_though_normal_pair_exists():
    """普通双路存在，但去掉停用光纤后只剩一条独立走廊：无法只切换一路。"""
    draft = make_draft(
        ["S", "T", "A", "B", "C"],
        [
            ("S", "A", 1, 1),  # 1 停用
            ("A", "T", 1, 1),  # 2
            ("S", "B", 2, 1),  # 3
            ("B", "C", 2, 1),  # 4
            ("C", "T", 2, 1),  # 5
            ("B", "T", 8, 1),  # 6（与 #3/#4/#5 同走廊，备选仍共享 S-B）
            ("A", "B", 3, 1),  # 7
        ],
        "S", "T", 1000,
    )
    assert select_redundant_pair(draft) is not None
    plan = plan_maintenance_switch(draft, 0)
    assert plan is None
    # 停用光纤之外的所有 S-T 路径都经过 B，无法同时容纳驻留路与替代路
    avoid = [p for p in enumerate_paths(draft) if 0 not in p.fibers]
    assert all("B" in p.nodes[1:-1] for p in avoid)


def test_outage_fiber_on_no_simple_path_is_infeasible():
    """停用光纤不在任何主控室→展柜简单路径上（末端悬垂段）：
    检修前双路不可能实际使用它，预案不可行。"""
    draft = make_draft(
        ["S", "T", "A", "B", "E"],
        [
            ("S", "A", 1, 1),  # 1
            ("A", "T", 1, 1),  # 2
            ("S", "B", 2, 1),  # 3
            ("B", "T", 2, 1),  # 4
            ("A", "E", 1, 1),  # 5 悬垂段，无 S-T 简单路径经过
            ("S", "T", 3, 1),  # 6
            ("A", "B", 1, 1),  # 7
        ],
        "S", "T", 1000,
    )
    assert plan_maintenance_switch(draft, 4) is None


def test_attenuation_limit_applies_in_both_phases():
    """替代路也必须满足衰减上限：超限的替代候选被剪枝。"""
    fibers = [(a, b, l, w) for a, b, l, w in MAINT_FIBERS]
    # #3/#8/#9（唯一可行的替代走廊 S-B-D-T）衰减抬高到使该路超限
    fibers[2] = ("S", "B", 2, 9)
    fibers[7] = ("B", "D", 2, 9)
    fibers[8] = ("D", "T", 2, 9)
    draft = make_draft(MAINT_NODES, fibers, "S", "T", 10)
    plan = plan_maintenance_switch(draft, 0)
    assert plan is None
