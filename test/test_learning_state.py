"""状态机单测：建档、讲解记录、判分、推进、拆解、报告、重置。"""
import json

import pytest

from tools import _store as S
from tools import learning_state as LS


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv(S.DATA_DIR_ENV, str(tmp_path / "data"))
    return tmp_path / "data"


def call(action, **payload):
    return LS._dispatch(action, payload)


def points(*titles):
    return [
        {"title": t, "acceptance": [f"能解释{t}"], "difficulty": 2, "est_minutes": 20}
        for t in titles
    ]


def make_plan(*titles, config=None):
    result = call(
        "create_plan",
        goal={"title": "测试目标", "daily_minutes": 30},
        knowledge_points=points(*titles) if titles else points("知识点一", "知识点二"),
        config=config,
    )
    assert result["ok"] is True
    return result


PASS_ITEMS = [
    {"qid": "q1", "type": "single", "user_answer": "B", "answer_key": "B"},
    {"qid": "q2", "type": "short", "rubric_score": 1.0},
]

FAIL_ITEMS = [
    {"qid": "q1", "type": "single", "user_answer": "A", "answer_key": "B", "tags": ["薄弱甲"]},
    {"qid": "q2", "type": "short", "rubric_score": 0.0, "tags": ["薄弱乙"]},
]


# --- 建档 -------------------------------------------------------------------


def test_create_plan_returns_id_and_first_point():
    result = make_plan("甲", "乙")
    assert result["plan_id"].startswith("pln-")
    assert result["total"] == 2
    assert result["current_kp"]["title"] == "甲"
    assert result["current_kp"]["status"] == "pending"
    assert result["progress"]["pending"] == 2


def test_create_plan_requires_goal_title():
    result = call("create_plan", goal={}, knowledge_points=points("甲"))
    assert result["ok"] is False
    assert result["error"] == "invalid_goal"
    assert "hint" in result


def test_create_plan_requires_knowledge_points():
    result = call("create_plan", goal={"title": "目标"}, knowledge_points=[])
    assert result["ok"] is False
    assert result["error"] == "empty_plan"


def test_create_plan_skips_points_without_title():
    result = call(
        "create_plan",
        goal={"title": "目标"},
        knowledge_points=[{"title": "有效"}, {"title": "  "}, {"acceptance": ["x"]}],
    )
    assert result["ok"] is True
    assert result["total"] == 1


def test_create_plan_applies_default_config():
    result = make_plan()
    assert result["config"]["mastery_threshold"] == 0.8
    assert result["config"]["max_reteach_rounds"] == 3


def test_create_plan_honours_config_override():
    result = make_plan(config={"mastery_threshold": 0.5, "max_split_depth": 1})
    assert result["config"]["mastery_threshold"] == 0.5
    assert result["config"]["max_split_depth"] == 1


def test_plan_persists_to_disk(isolated_root):
    result = make_plan("甲")
    assert S.plan_path(result["plan_id"]).is_file()


def test_plan_survives_new_session():
    """模拟关掉会话后重开：重新 load_plan 必须拿到同一份进度。"""
    created = make_plan("甲", "乙")
    call("record_teach", angle="intuition")
    call("submit_answers", items=PASS_ITEMS)

    reloaded = call("load_plan")
    assert reloaded["ok"] is True
    assert reloaded["plan_id"] == created["plan_id"]
    assert reloaded["current_kp"]["status"] == "mastered"
    assert reloaded["current_kp"]["mastery"] == 1.0


# --- 无计划时的降级 ---------------------------------------------------------


def test_actions_without_plan_return_guided_error():
    for action in ("load_plan", "get_current", "submit_answers", "advance", "get_summary"):
        result = call(action)
        assert result["ok"] is False
        assert result["error"] == "no_active_plan"
        assert "create_plan" in result["hint"]


def test_unknown_action_is_rejected():
    result = call("nonsense")
    assert result["ok"] is False
    assert result["error"] == "unknown_action"


def test_unavailable_plan_reports_reason():
    result = call("load_plan", plan_id="pln-does-not-exist")
    assert result["ok"] is False
    assert result["error"] == "plan_unavailable"


# --- 讲解记录 ---------------------------------------------------------------


def test_record_teach_tracks_angles_without_duplicates():
    make_plan("甲")
    first = call("record_teach", angle="intuition")
    assert first["angles_tried"] == ["intuition"]
    assert first["next_angle"] == "visual"

    call("record_teach", angle="intuition")   # 重复记录不叠加
    third = call("record_teach", angle="visual")
    assert third["angles_tried"] == ["intuition", "visual"]
    assert third["next_angle"] == "example"


def test_record_teach_moves_point_to_learning():
    make_plan("甲")
    call("record_teach", angle="intuition")
    assert call("get_current")["kp"]["status"] == "learning"


def test_record_teach_rejects_unknown_angle():
    make_plan("甲")
    result = call("record_teach", angle="telepathy")
    assert result["ok"] is False
    assert result["error"] == "invalid_angle"
    assert "intuition" in result["hint"]


# --- 判分与推进 -------------------------------------------------------------


def test_passing_answers_master_the_point():
    make_plan("甲", "乙")
    result = call("submit_answers", items=PASS_ITEMS)
    assert result["verdict"] == "pass"
    assert result["action"] == "advance"
    assert result["kp_status"] == "mastered"
    assert result["mastery"] == 1.0


def test_advance_is_blocked_until_mastered():
    make_plan("甲", "乙")
    result = call("advance")
    assert result["ok"] is False
    assert result["error"] == "not_mastered"
    assert "submit_answers" in result["hint"]


def test_advance_moves_to_next_point_after_mastery():
    make_plan("甲", "乙")
    call("submit_answers", items=PASS_ITEMS)

    result = call("advance")
    assert result["ok"] is True
    assert result["all_done"] is False
    assert result["next_kp"]["title"] == "乙"
    assert result["previous_kp"]["title"] == "甲"
    assert result["progress"]["mastered"] == 1
    assert call("get_current")["kp"]["title"] == "乙"


def test_advance_reports_all_done_after_last_point():
    make_plan("甲")
    call("submit_answers", items=PASS_ITEMS)

    result = call("advance")
    assert result["ok"] is True
    assert result["all_done"] is True
    assert result["progress"]["percent"] == 1.0
    assert call("get_current")["all_done"] is True


def test_submit_answers_rejects_resubmission_after_mastery():
    make_plan("甲")
    call("submit_answers", items=PASS_ITEMS)
    result = call("submit_answers", items=PASS_ITEMS)
    assert result["ok"] is False
    assert result["error"] == "already_mastered"


def test_submit_answers_requires_items():
    make_plan("甲")
    result = call("submit_answers", items=[])
    assert result["ok"] is False
    assert result["error"] == "empty_answers"


def test_wrong_answers_are_recorded_in_wrong_book():
    make_plan("甲")
    result = call("submit_answers", items=FAIL_ITEMS)
    assert result["verdict"] == "fail"
    assert len(result["wrong_now"]) == 2
    assert result["weak_tags"] == sorted({"薄弱甲", "薄弱乙"})

    summary = call("get_summary")
    assert len(summary["wrong_book"]) == 2
    assert {entry["tag"] for entry in summary["weak_top"]} == {"薄弱甲", "薄弱乙"}


def test_attempt_round_advances_with_each_submission():
    make_plan("甲", config={"max_reteach_rounds": 5})
    assert call("submit_answers", items=FAIL_ITEMS)["round"] == 1
    assert call("submit_answers", items=FAIL_ITEMS)["round"] == 2
    assert call("submit_answers", items=FAIL_ITEMS)["round"] == 3


# --- 收敛：换角度重讲 → 拆解 → 跳过 -----------------------------------------


def test_failing_loop_rotates_angles_then_suggests_split():
    make_plan("甲", config={"max_reteach_rounds": 2, "max_split_depth": 1})

    first = call("submit_answers", items=FAIL_ITEMS)
    assert first["action"] == "reteach"
    assert first["suggested_angle"] == "intuition"
    assert first["reteach_rounds"] == 1

    second = call("submit_answers", items=FAIL_ITEMS)
    assert second["action"] == "reteach"
    assert second["suggested_angle"] == "visual"
    assert second["reteach_rounds"] == 2

    third = call("submit_answers", items=FAIL_ITEMS)
    assert third["action"] == "split"
    assert third["suggested_angle"] is None


def test_angle_rotation_does_not_depend_on_record_teach():
    """健壮性：模型漏调 record_teach 时，建议角度仍须轮换，不能退化成重复同一讲法。"""
    make_plan("甲", config={"max_reteach_rounds": 5})

    angles = [call("submit_answers", items=FAIL_ITEMS)["suggested_angle"] for _ in range(3)]
    assert angles == ["intuition", "visual", "example"]


def test_record_teach_and_suggestion_together_still_rotate():
    """正常流程：讲解记录 + 建议角度取并集，不重复也不跳号。"""
    make_plan("甲", config={"max_reteach_rounds": 5})
    call("record_teach", angle="intuition")

    assert call("submit_answers", items=FAIL_ITEMS)["suggested_angle"] == "visual"
    call("record_teach", angle="visual")
    assert call("submit_answers", items=FAIL_ITEMS)["suggested_angle"] == "example"


def test_split_exhaustion_falls_back_to_skip():
    make_plan("甲", config={"max_reteach_rounds": 0, "max_split_depth": 0})
    result = call("submit_answers", items=FAIL_ITEMS)
    assert result["action"] == "skip"
    assert result["kp_status"] == "skipped"


def test_skipped_point_can_be_advanced_past():
    make_plan("甲", "乙", config={"max_reteach_rounds": 0, "max_split_depth": 0})
    call("submit_answers", items=FAIL_ITEMS)

    result = call("advance")
    assert result["ok"] is True
    assert result["next_kp"]["title"] == "乙"


# --- 拆解 -------------------------------------------------------------------


def test_split_kp_inserts_children_after_parent():
    make_plan("甲", "乙", config={"max_reteach_rounds": 0, "max_split_depth": 2})
    call("submit_answers", items=FAIL_ITEMS)

    result = call("split_kp", sub_points=points("甲-小一", "甲-小二"))
    assert result["ok"] is True
    assert result["new_kp_ids"] == ["kp-001-1", "kp-001-2"]
    assert result["current_kp"]["title"] == "甲-小一"

    summary = call("get_summary")
    titles = [row["title"] for row in summary["kp_table"]]
    assert titles == ["甲", "甲-小一", "甲-小二", "乙"]
    assert summary["kp_table"][0]["status"] == "split"
    assert summary["progress"]["split"] == 1
    assert summary["progress"]["total"] == 4


def test_split_children_inherit_depth_and_parent():
    make_plan("甲", config={"max_reteach_rounds": 0, "max_split_depth": 2})
    call("submit_answers", items=FAIL_ITEMS)
    call("split_kp", sub_points=points("小一", "小二"))

    child = call("get_current")["kp"]
    assert child["split_depth"] == 1
    assert child["status"] == "pending"


def test_split_kp_enforces_depth_limit():
    make_plan("甲", config={"max_reteach_rounds": 0, "max_split_depth": 0})
    call("submit_answers", items=FAIL_ITEMS)

    result = call("split_kp", sub_points=points("小一", "小二"))
    assert result["ok"] is False
    assert result["error"] == "split_depth_exceeded"
    assert "advance" in result["hint"]


def test_split_kp_requires_at_least_two_children():
    make_plan("甲")
    result = call("split_kp", sub_points=points("只有一个"))
    assert result["ok"] is False
    assert result["error"] == "invalid_sub_points"


def test_child_point_can_be_mastered_independently():
    make_plan("甲", config={"max_reteach_rounds": 0, "max_split_depth": 2})
    call("submit_answers", items=FAIL_ITEMS)
    call("split_kp", sub_points=points("小一", "小二"))

    result = call("submit_answers", items=PASS_ITEMS)
    assert result["verdict"] == "pass"
    assert result["kp_id"] == "kp-001-1"


# --- 报告 -------------------------------------------------------------------


def test_summary_shape_and_progress_math():
    make_plan("甲", "乙", "丙")
    call("submit_answers", items=PASS_ITEMS)
    call("advance")

    summary = call("get_summary")
    assert summary["ok"] is True
    assert summary["progress"]["total"] == 3
    assert summary["progress"]["mastered"] == 1
    assert summary["progress"]["pending"] == 2
    assert summary["progress"]["percent"] == round(1 / 3, 4)
    assert len(summary["kp_table"]) == 3
    assert summary["goal"]["title"] == "测试目标"


def test_summary_excludes_split_containers_from_percent():
    make_plan("甲", config={"max_reteach_rounds": 0, "max_split_depth": 2})
    call("submit_answers", items=FAIL_ITEMS)
    call("split_kp", sub_points=points("小一", "小二"))
    call("submit_answers", items=PASS_ITEMS)

    progress = call("get_summary")["progress"]
    assert progress["total"] == 3
    assert progress["split"] == 1
    assert progress["mastered"] == 1
    assert progress["percent"] == 0.5      # 1 / (3 - 1)


# --- 多计划与重置 -----------------------------------------------------------


def test_list_plans_reports_active():
    created = make_plan("甲")
    listing = call("list_plans")
    assert listing["active_plan_id"] == created["plan_id"]
    assert len(listing["plans"]) == 1
    assert listing["plans"][0]["title"] == "测试目标"


def test_set_active_switches_between_plans():
    first = make_plan("甲")
    second = call(
        "create_plan",
        goal={"title": "第二个目标"},
        knowledge_points=points("乙"),
    )
    assert call("list_plans")["active_plan_id"] == second["plan_id"]

    call("set_active", plan_id=first["plan_id"])
    assert call("load_plan")["goal"]["title"] == "测试目标"


def test_set_active_requires_plan_id():
    result = call("set_active")
    assert result["ok"] is False
    assert result["error"] == "missing_plan_id"


def test_reset_requires_explicit_confirmation():
    make_plan("甲")
    result = call("reset")
    assert result["ok"] is False
    assert result["error"] == "confirm_required"
    assert call("load_plan")["ok"] is True      # 计划仍在


def test_reset_with_confirmation_deletes_plan():
    created = make_plan("甲")
    result = call("reset", confirm=True)
    assert result["ok"] is True
    assert result["deleted"] is True
    assert S.load_plan(created["plan_id"])[0] is False
    assert call("load_plan")["error"] == "no_active_plan"


# --- 工具封装 ---------------------------------------------------------------


def test_tool_wrapper_returns_json_string():
    payload = json.loads(LS.learning_state(action="list_plans"))
    assert payload["ok"] is True


def test_tool_wrapper_reports_errors_as_json():
    payload = json.loads(LS.learning_state(action="bogus"))
    assert payload["ok"] is False
    assert payload["error"] == "unknown_action"
