"""掌握度算法单测：判分确定性、高权重门槛、尝试惩罚、角度轮换、收敛路径。"""
from tools import _mastery as M


def obj(qid, qtype, user, key, tags=None):
    item = {"qid": qid, "type": qtype, "user_answer": user, "answer_key": key}
    if tags:
        item["tags"] = tags
    return item


def short(qid, score, tags=None):
    item = {"qid": qid, "type": "short", "rubric_score": score}
    if tags:
        item["tags"] = tags
    return item


# --- 客观题判分 -------------------------------------------------------------


def test_single_grading_is_case_insensitive():
    assert M.grade_objective(obj("q", "single", "b", "B")) is True
    assert M.grade_objective(obj("q", "single", "A", "B")) is False


def test_multi_grading_accepts_equivalent_forms():
    # "A,C" / "AC" / ["A","C"] 必须判为同一作答
    assert M.grade_objective(obj("q", "multi", "A,C", "AC")) is True
    assert M.grade_objective(obj("q", "multi", "AC", "A,C")) is True
    assert M.grade_objective(obj("q", "multi", ["A", "C"], "AC")) is True
    # 少选 / 多选 / 错选都算错
    assert M.grade_objective(obj("q", "multi", "A", "AC")) is False
    assert M.grade_objective(obj("q", "multi", "ACD", "AC")) is False
    assert M.grade_objective(obj("q", "multi", "BD", "AC")) is False


def test_subjective_items_are_not_graded_objectively():
    assert M.grade_objective(short("q", 0.6)) is None


def test_objective_grading_is_deterministic():
    item = obj("q", "multi", "C,A", "AC")
    assert len({M.grade_objective(dict(item)) for _ in range(50)}) == 1


def test_missing_answer_is_wrong():
    assert M.grade_objective({"type": "single", "answer_key": "B"}) is False


# --- 达标与门槛 -------------------------------------------------------------


def test_empty_items_fail():
    result = M.evaluate_attempt([], round_no=1)
    assert result["verdict"] == "fail"
    assert result["mastery"] == 0.0
    assert result["action"] == M.ACTION_RETEACH
    assert "本轮没有有效作答" in result["fail_reasons"]


def test_all_correct_passes_and_advances():
    items = [
        obj("q1", "single", "B", "B"),
        obj("q2", "multi", "A,C", "AC"),
        short("q3", 1.0),
    ]
    result = M.evaluate_attempt(items, round_no=1)
    assert result["weighted_accuracy"] == 1.0
    assert result["mastery"] == 1.0
    assert result["verdict"] == "pass"
    assert result["action"] == M.ACTION_ADVANCE
    assert result["suggested_angle"] is None


def test_mastery_exactly_at_threshold_passes():
    result = M.evaluate_attempt([short("q1", 0.8)], round_no=1)
    assert result["mastery"] == 0.8
    assert result["verdict"] == "pass"


def test_heavy_gate_blocks_high_total_score():
    """抗刷分：选择题全对但简答全错，总分达标也必须判 fail。"""
    items = [obj(f"q{i}", "single", "B", "B") for i in range(10)]
    items.append(short("q11", 0.0))
    result = M.evaluate_attempt(items, round_no=1)
    assert result["mastery"] >= 0.8          # 总分其实达标了
    assert result["verdict"] == "fail"       # 但门槛把它拦下
    assert result["action"] == M.ACTION_RETEACH
    assert any("高权重题型" in reason for reason in result["fail_reasons"])


def test_heavy_gate_not_applied_without_heavy_items():
    items = [obj(f"q{i}", "single", "B", "B") for i in range(5)]
    result = M.evaluate_attempt(items, round_no=1)
    assert result["heavy_avg"] is None
    assert result["verdict"] == "pass"


# --- 尝试次数惩罚 -----------------------------------------------------------


def test_penalty_progression_and_floor():
    assert M.penalty_for_round(1) == 1.0
    assert M.penalty_for_round(2) == 0.9
    assert M.penalty_for_round(4) == 0.7
    assert M.penalty_for_round(9) == 0.7   # 触底不再下降
    assert M.penalty_for_round(0) == 1.0   # 非法轮次按第 1 轮处理


def test_penalty_lowers_mastery_across_rounds():
    items = [short("q1", 1.0)]
    first = M.evaluate_attempt(items, round_no=1)
    third = M.evaluate_attempt(items, round_no=3)
    assert first["mastery"] == 1.0
    assert third["mastery"] == 0.8
    assert third["weighted_accuracy"] == first["weighted_accuracy"]


# --- 角度轮换 ---------------------------------------------------------------


def test_next_angle_follows_sequence():
    assert M.next_angle([]) == "intuition"
    assert M.next_angle(["intuition"]) == "visual"
    assert M.next_angle(["intuition", "visual"]) == "example"


def test_next_angle_never_repeats_a_tried_angle():
    tried = []
    for _ in range(len(M.TEACH_ANGLES)):
        angle = M.next_angle(tried)
        assert angle not in tried
        tried.append(angle)
    assert M.next_angle(tried) == M.TEACH_ANGLES[0]   # 耗尽后回落


def test_suggested_angle_is_untried():
    result = M.evaluate_attempt(
        [short("q1", 0.0)],
        round_no=2,
        angles_tried=["intuition", "visual"],
    )
    assert result["action"] == M.ACTION_RETEACH
    assert result["suggested_angle"] == "example"


# --- 收敛路径（两级兜底） ---------------------------------------------------


def test_action_escalates_reteach_split_skip():
    failing = [short("q1", 0.0)]
    cfg = M.normalize_config({"max_reteach_rounds": 3, "max_split_depth": 2})

    reteach = M.evaluate_attempt(failing, round_no=2, config=cfg, reteach_rounds=0, split_depth=0)
    assert reteach["action"] == M.ACTION_RETEACH

    split = M.evaluate_attempt(failing, round_no=4, config=cfg, reteach_rounds=3, split_depth=0)
    assert split["action"] == M.ACTION_SPLIT

    skip = M.evaluate_attempt(failing, round_no=6, config=cfg, reteach_rounds=3, split_depth=2)
    assert skip["action"] == M.ACTION_SKIP


# --- 薄弱点聚合 -------------------------------------------------------------


def test_weak_tags_collects_low_scoring_items_only():
    items = [
        obj("q1", "single", "A", "B", tags=["子概念甲"]),      # 0.0 → 计入
        obj("q2", "single", "B", "B", tags=["子概念乙"]),      # 1.0 → 不计入
        short("q3", 0.3, tags=["子概念丙"]),                    # 0.3 → 计入
        short("q4", 0.6, tags=["子概念丁"]),                    # 0.6 → 不计入（等于阈值）
    ]
    result = M.evaluate_attempt(items, round_no=1)
    assert result["weak_tags"] == sorted({"子概念甲", "子概念丙"})


def test_weak_tags_are_deduplicated_and_sorted():
    items = [
        obj("q1", "single", "A", "B", tags=["乙"]),
        obj("q2", "single", "A", "B", tags=["甲"]),
        obj("q3", "single", "A", "B", tags=["甲"]),
    ]
    assert M.evaluate_attempt(items, round_no=1)["weak_tags"] == sorted({"甲", "乙"})


# --- 配置与权重 -------------------------------------------------------------


def test_config_is_clamped_to_valid_range():
    cfg = M.normalize_config({"mastery_threshold": 5, "heavy_type_floor": -1, "max_reteach_rounds": -3})
    assert cfg["mastery_threshold"] == 1.0
    assert cfg["heavy_type_floor"] == 0.0
    assert cfg["max_reteach_rounds"] == 0


def test_config_falls_back_on_garbage_input():
    cfg = M.normalize_config({"heavy_types": "not-a-list", "max_split_depth": None})
    assert cfg["heavy_types"] == M.DEFAULT_CONFIG["heavy_types"]
    assert cfg["max_split_depth"] == M.DEFAULT_CONFIG["max_split_depth"]


def test_explicit_weight_overrides_type_weight():
    assert M.item_weight({"type": "single", "weight": 5}) == 5.0
    assert M.item_weight({"type": "apply"}) == M.TYPE_WEIGHTS["apply"]
    assert M.item_weight({"type": "unknown"}) == M.DEFAULT_TYPE_WEIGHT
    assert M.item_weight({"type": "single", "weight": -1}) == M.TYPE_WEIGHTS["single"]


def test_clamp01_handles_garbage():
    assert M.clamp01(None) == 0.0
    assert M.clamp01("abc") == 0.0
    assert M.clamp01(float("nan")) == 0.0
    assert M.clamp01(-3) == 0.0
    assert M.clamp01(3) == 1.0
    assert M.clamp01(0.42) == 0.42
