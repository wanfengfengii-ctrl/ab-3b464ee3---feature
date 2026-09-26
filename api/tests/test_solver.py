"""裁决算法单元测试。"""

from app.models import Draft, Fiber
from app.solver import enumerate_paths, select_redundant_pair


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
