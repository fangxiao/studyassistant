"""掌握度确定性计算核心（纯函数，无 IO）。

设计约束见 docs/adr/0002-deterministic-mastery-scoring.md：

- 本模块是**纯函数**集合，不做任何 IO，不依赖模型的主观措辞。
- 模型只负责给主观题打 0~1 的分（依据评分要点）；
  **聚合、达标判定、下一步动作**全部在这里决定。
- 「学会」的判定权不在模型手里 —— 模型无法通过措辞让学习者过关。
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

# --- 常量 -------------------------------------------------------------------

#: 客观题题型：判分完全确定性，由 answer_key 比对得出
OBJECTIVE_TYPES: tuple[str, ...] = ("single", "multi")

#: 题型基准权重。权重越高，越能代表「真会」。
TYPE_WEIGHTS: dict[str, float] = {
    "single": 1.0,   # 单选：能认出来
    "multi": 1.4,    # 多选：边界清晰
    "short": 2.0,    # 简答：能自己说出来
    "apply": 2.5,    # 应用/推导：能迁移
}
DEFAULT_TYPE_WEIGHT = 1.0

#: 讲解角度轮换序列（见 docs/adr/0003）。按序取第一个「未试过」的角度。
TEACH_ANGLES: tuple[str, ...] = (
    "intuition",  # 直觉类比
    "visual",     # 图示
    "example",    # 实例推演
    "contrast",   # 对比辨析
    "formal",     # 严谨定义
    "socratic",   # 追问引导
)

#: 下一步动作
ACTION_ADVANCE = "advance"    # 达标 → 解锁下一个知识点
ACTION_RETEACH = "reteach"    # 未达标 → 换角度重讲
ACTION_SPLIT = "split"        # 重讲多次无效 → 降级拆解为更小颗粒度
ACTION_SKIP = "skip"          # 拆解也到顶 → 标记薄弱点，跳过以推进计划

#: 尝试次数惩罚
PENALTY_STEP = 0.1
PENALTY_FLOOR = 0.7

#: 默认配置。全部可在创建计划时覆盖。
DEFAULT_CONFIG: dict[str, Any] = {
    "mastery_threshold": 0.8,      # 达标线
    "heavy_type_floor": 0.6,       # 高权重题型得分率门槛
    "heavy_types": ["short", "apply"],
    "max_reteach_rounds": 3,       # 超过则建议拆解
    "max_split_depth": 2,          # 拆解层数上限，防无限下钻
    "weak_tag_floor": 0.6,         # 低于此得分率的题目标签计入薄弱点
}


# --- 基础工具 ---------------------------------------------------------------


def clamp01(value: Any) -> float:
    """把任意输入夹到 [0, 1]。非数字 / NaN 一律视为 0。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    if num != num:  # NaN
        return 0.0
    return max(0.0, min(1.0, num))


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_config(config: dict | None = None) -> dict[str, Any]:
    """合并并校验配置，保证下游拿到的一定是合法值。"""
    cfg: dict[str, Any] = dict(DEFAULT_CONFIG)
    if isinstance(config, dict):
        for key, value in config.items():
            if key in cfg and value is not None:
                cfg[key] = value

    cfg["mastery_threshold"] = clamp01(cfg["mastery_threshold"])
    cfg["heavy_type_floor"] = clamp01(cfg["heavy_type_floor"])
    cfg["weak_tag_floor"] = clamp01(cfg["weak_tag_floor"])

    heavy = cfg.get("heavy_types")
    cfg["heavy_types"] = [str(t) for t in heavy] if isinstance(heavy, (list, tuple)) else list(
        DEFAULT_CONFIG["heavy_types"]
    )

    cfg["max_reteach_rounds"] = max(0, _as_int(cfg["max_reteach_rounds"], 3))
    cfg["max_split_depth"] = max(0, _as_int(cfg["max_split_depth"], 2))
    return cfg


# --- 单题判分 ---------------------------------------------------------------


def item_weight(item: dict) -> float:
    """题目权重：显式 weight 优先，否则按题型基准权重。"""
    explicit = item.get("weight")
    if isinstance(explicit, (int, float)) and not isinstance(explicit, bool) and explicit > 0:
        return float(explicit)
    return TYPE_WEIGHTS.get(str(item.get("type") or ""), DEFAULT_TYPE_WEIGHT)


def _normalize_options(value: Any) -> list[str]:
    """把多选作答归一化为排序去重的大写字母集合，便于确定性比对。

    兼容三种常见写法：``"A,C"``、``["A", "C"]``、``"ACD"``（紧凑串）。
    归一化后 ``"A,C"`` 与 ``"AC"`` 会判为同一作答。
    """
    if isinstance(value, str):
        parts = (
            value.replace(",", " ")
            .replace("，", " ")
            .replace("、", " ")
            .replace(";", " ")
            .split()
        )
    elif isinstance(value, (list, tuple, set)):
        parts = [str(v) for v in value]
    elif value is None:
        parts = []
    else:
        parts = [str(value)]

    tokens = {p.strip().upper() for p in parts if p.strip()}
    # 紧凑写法兼容：单一 token 且是多字母纯英文时，拆成单字母集合
    if len(tokens) == 1:
        only = next(iter(tokens))
        if len(only) > 1 and only.isascii() and only.isalpha():
            tokens = set(only)
    return sorted(tokens)


def grade_objective(item: dict) -> bool | None:
    """客观题确定性判分。

    返回 ``True`` / ``False`` 表示判分结果；返回 ``None`` 表示这不是客观题
    （主观题交由模型按评分要点打分）。
    """
    qtype = str(item.get("type") or "")
    if qtype not in OBJECTIVE_TYPES:
        return None

    answer_key = item.get("answer_key")
    user_answer = item.get("user_answer")
    if answer_key is None or user_answer is None:
        return False

    if qtype == "single":
        return str(user_answer).strip().upper() == str(answer_key).strip().upper()
    return _normalize_options(user_answer) == _normalize_options(answer_key)


def item_score(item: dict) -> float:
    """单题得分率 [0, 1]。

    客观题由 ``correct`` 决定（调用方应先经 :func:`grade_objective` 判定）；
    主观题取 ``rubric_score``（模型依据评分要点给出）。
    """
    if str(item.get("type") or "") in OBJECTIVE_TYPES:
        return 1.0 if item.get("correct") is True else 0.0
    if item.get("rubric_score") is not None:
        return clamp01(item["rubric_score"])
    return 1.0 if item.get("correct") is True else 0.0


# --- 轮次与角度 -------------------------------------------------------------


def penalty_for_round(round_no: int) -> float:
    """尝试次数惩罚：第 1 轮无惩罚，此后每轮 -0.1，下限 0.7。

    作用：让「反复试到过」的代价递增，避免靠穷举蒙对。
    """
    rounds = max(1, _as_int(round_no, 1))
    return max(PENALTY_FLOOR, round(1.0 - PENALTY_STEP * (rounds - 1), 4))


def next_angle(tried: Iterable[str] | None) -> str:
    """返回序列中第一个尚未使用过的讲解角度。

    轮换耗尽后回落到序列首位 —— 但此时必然已触发 ``split``（见 ADR-0003），
    因此不会退化成「重复同一讲法」。
    """
    tried_set = {str(a) for a in (tried or [])}
    for angle in TEACH_ANGLES:
        if angle not in tried_set:
            return angle
    return TEACH_ANGLES[0]


# --- 主流程 -----------------------------------------------------------------


def evaluate_attempt(
    items: Sequence[dict] | None,
    round_no: int = 1,
    config: dict | None = None,
    angles_tried: Iterable[str] | None = None,
    reteach_rounds: int = 0,
    split_depth: int = 0,
) -> dict[str, Any]:
    """一次验证的完整判定：判分 → 掌握度 → 达标 → 下一步动作。

    Args:
        items: 本轮作答明细，每项含 ``type`` / ``user_answer`` / ``answer_key``
            （客观题）或 ``rubric_score``（主观题），可选 ``qid`` / ``weight`` / ``tags``。
        round_no: 该知识点第几轮验证（1-based）。
        config: 计划配置，缺省用 :data:`DEFAULT_CONFIG`。
        angles_tried: 该知识点已用过的讲解角度。
        reteach_rounds: 该知识点已重讲次数。
        split_depth: 该知识点已拆解层数。

    Returns:
        含 ``mastery`` / ``verdict`` / ``action`` / ``suggested_angle`` /
        ``weak_tags`` / ``fail_reasons`` 等字段的判定结果。
    """
    cfg = normalize_config(config)
    raw_items = [i for i in (items or []) if isinstance(i, dict)]

    # ① 判分：客观题确定性判定，主观题取模型给的 rubric_score
    graded: list[dict[str, Any]] = []
    for raw in raw_items:
        entry = dict(raw)
        objective = grade_objective(entry)
        if objective is not None:
            entry["correct"] = objective
        entry["weight"] = item_weight(entry)
        entry["score"] = round(item_score(entry), 4)
        graded.append(entry)

    # ② 加权得分率
    total_weight = sum(e["weight"] for e in graded)
    weighted_accuracy = (
        round(sum(e["weight"] * e["score"] for e in graded) / total_weight, 4)
        if total_weight > 0
        else 0.0
    )

    # ③ 高权重题型门槛：防止靠选择题刷分
    heavy = [e for e in graded if str(e.get("type") or "") in cfg["heavy_types"]]
    heavy_avg = round(sum(e["score"] for e in heavy) / len(heavy), 4) if heavy else None
    heavy_gate_failed = heavy_avg is not None and heavy_avg < cfg["heavy_type_floor"]

    # ④ 尝试次数惩罚
    penalty = penalty_for_round(round_no)

    # ⑤ 掌握度
    mastery = round(weighted_accuracy * penalty, 4)

    # ⑥ 达标判定
    fail_reasons: list[str] = []
    if not graded:
        fail_reasons.append("本轮没有有效作答")
    if heavy_gate_failed:
        heavy_names = "/".join(cfg["heavy_types"])
        fail_reasons.append(
            f"高权重题型（{heavy_names}）平均得分率 {heavy_avg:.2f} 低于门槛 {cfg['heavy_type_floor']:.2f}"
        )
    if mastery < cfg["mastery_threshold"]:
        fail_reasons.append(
            f"掌握度 {mastery:.2f} 低于达标线 {cfg['mastery_threshold']:.2f}"
        )

    passed = not fail_reasons

    # ⑦ 下一步动作（两级收敛，见 ADR-0003）
    suggested_angle: str | None = None
    if passed:
        action = ACTION_ADVANCE
    elif reteach_rounds < cfg["max_reteach_rounds"]:
        action = ACTION_RETEACH
        suggested_angle = next_angle(angles_tried)
    elif split_depth < cfg["max_split_depth"]:
        action = ACTION_SPLIT
    else:
        action = ACTION_SKIP

    # ⑧ 薄弱子点聚合
    weak_tags = sorted(
        {
            str(tag)
            for e in graded
            if e["score"] < cfg["weak_tag_floor"]
            for tag in (e.get("tags") or [])
            if str(tag).strip()
        }
    )

    return {
        "items": graded,
        "weighted_accuracy": weighted_accuracy,
        "heavy_avg": heavy_avg,
        "penalty": penalty,
        "mastery": mastery,
        "threshold": cfg["mastery_threshold"],
        "verdict": "pass" if passed else "fail",
        "action": action,
        "suggested_angle": suggested_angle,
        "weak_tags": weak_tags,
        "fail_reasons": fail_reasons,
    }
