"""学习状态机工具：计划 / 进度 / 错题本 / 掌握度判定。

职责边界（见 docs/design/studyassistant.md §5）：

- 本工具负责**结构化状态的读写**与**达标判定**。
- 技能（skills/）负责讲解与出题的 Prompt 思考链，**不得**自行宣称「你已掌握」——
  达标与否只能由本工具返回。

实现上分为两层：
- ``_dispatch`` —— 纯逻辑，无装饰器依赖，便于单测直接调用。
- ``learning_state`` —— ``@tool`` 薄封装，把结果序列化为 JSON 字符串。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# 平台可能以「包」或「按文件路径」两种方式加载本模块，这里两种都兼容。
try:  # pragma: no cover - 取决于平台加载方式
    from . import _mastery, _store
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _mastery  # type: ignore[no-redef]
    import _store  # type: ignore[no-redef]

from agentplatform.sdk import tool

# --- 常量 -------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_LEARNING = "learning"
STATUS_MASTERED = "mastered"
STATUS_SKIPPED = "skipped"
STATUS_SPLIT = "split"

#: 允许从当前知识点推进到下一个的状态
ADVANCEABLE = (STATUS_MASTERED, STATUS_SKIPPED)

ACTIONS = (
    "create_plan",
    "load_plan",
    "get_current",
    "record_teach",
    "submit_answers",
    "advance",
    "split_kp",
    "get_summary",
    "list_plans",
    "set_active",
    "reset",
)


# --- 返回体构造 -------------------------------------------------------------


def _ok(**payload: Any) -> dict:
    return {"ok": True, **payload}


def _err(code: str, message: str, hint: str | None = None) -> dict:
    result: dict[str, Any] = {"ok": False, "error": code, "message": message}
    if hint:
        result["hint"] = hint
    return result


def _pos_int(value: Any, default: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return num if num > 0 else default


def _bounded_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))


# --- 计划结构辅助 -----------------------------------------------------------


def _normalize_kp(raw: dict, fallback_id: str, order: int) -> dict | None:
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    acceptance = [
        str(a).strip() for a in (raw.get("acceptance") or []) if str(a).strip()
    ]
    return {
        "id": str(raw.get("id") or fallback_id).strip(),
        "title": title,
        "parent_id": raw.get("parent_id") or None,
        "order": order,
        "difficulty": _bounded_int(raw.get("difficulty"), 1, 5, 3),
        "est_minutes": _pos_int(raw.get("est_minutes"), 20),
        "acceptance": acceptance,
        "status": STATUS_PENDING,
        "mastery": 0.0,
        "teach_angles_tried": [],
        "angles_suggested": [],
        "reteach_rounds": 0,
        "split_depth": _bounded_int(raw.get("split_depth"), 0, 99, 0),
        "attempts": [],
        "wrong_points": [],
    }


def _normalize_kps(raw_list: Any) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_list or [], start=1):
        if not isinstance(raw, dict):
            continue
        kp = _normalize_kp(raw, f"kp-{index:03d}", len(out) + 1)
        if kp is None:
            continue
        base_id = kp["id"]
        while kp["id"] in seen:
            kp["id"] = f"{kp['id']}x"
        seen.add(kp["id"])
        out.append(kp)
    return out


def _normalize_goal(raw: Any) -> dict:
    goal = raw if isinstance(raw, dict) else {}
    return {
        "title": str(goal.get("title") or "").strip(),
        "deadline": str(goal.get("deadline") or "").strip() or None,
        "daily_minutes": _pos_int(goal.get("daily_minutes"), 30),
        "current_level": str(goal.get("current_level") or "beginner").strip(),
        "notes": str(goal.get("notes") or "").strip() or None,
    }


def _find_kp(plan: dict, kp_id: str | None) -> dict | None:
    target = kp_id or plan.get("current_kp_id")
    if not target:
        return None
    for kp in plan.get("knowledge_points", []):
        if kp.get("id") == target:
            return kp
    return None


def _current_kp(plan: dict) -> dict | None:
    return _find_kp(plan, plan.get("current_kp_id"))


def _angles_used(kp: dict) -> list[str]:
    """已用过**或已建议过**的讲解角度。

    把「已建议」也算进去，是为了让换角度重讲不依赖模型是否老实调用了
    ``record_teach`` —— 否则模型一旦漏调，``next_angle`` 会一直返回同一个角度，
    「换一种讲法」就退化成了「重复同一种讲法」（见 ADR-0003）。
    """
    used: list[str] = []
    for angle in list(kp.get("teach_angles_tried") or []) + list(kp.get("angles_suggested") or []):
        if angle not in used:
            used.append(angle)
    return used


def _next_pending(plan: dict, after_order: int | None = None) -> dict | None:
    """按 order 找下一个尚未开始的知识点。"""
    candidates = [
        kp for kp in plan.get("knowledge_points", []) if kp.get("status") == STATUS_PENDING
    ]
    if after_order is not None:
        candidates = [kp for kp in candidates if kp.get("order", 0) > after_order]
    candidates.sort(key=lambda kp: kp.get("order", 0))
    return candidates[0] if candidates else None


def _kp_brief(kp: dict | None) -> dict | None:
    if kp is None:
        return None
    return {
        "id": kp.get("id"),
        "title": kp.get("title"),
        "order": kp.get("order"),
        "difficulty": kp.get("difficulty"),
        "est_minutes": kp.get("est_minutes"),
        "acceptance": kp.get("acceptance", []),
        "status": kp.get("status"),
        "mastery": kp.get("mastery", 0.0),
        "reteach_rounds": kp.get("reteach_rounds", 0),
        "split_depth": kp.get("split_depth", 0),
    }


def _progress(plan: dict) -> dict:
    counts = {
        STATUS_PENDING: 0,
        STATUS_LEARNING: 0,
        STATUS_MASTERED: 0,
        STATUS_SKIPPED: 0,
        STATUS_SPLIT: 0,
    }
    for kp in plan.get("knowledge_points", []):
        status = kp.get("status", STATUS_PENDING)
        counts[status] = counts.get(status, 0) + 1

    # split 是结构容器（被拆解掉的父知识点），不计入学习单元分母
    denominator = len(plan.get("knowledge_points", [])) - counts[STATUS_SPLIT]
    done = counts[STATUS_MASTERED] + counts[STATUS_SKIPPED]
    return {
        "total": len(plan.get("knowledge_points", [])),
        **counts,
        "done": done,
        "percent": round(done / denominator, 4) if denominator > 0 else 0.0,
    }


def _history(plan: dict, event: str, **extra: Any) -> None:
    plan.setdefault("history", []).append({"ts": _store.now_iso(), "event": event, **extra})


def _load_or_error(plan_id: str | None) -> tuple[dict | None, dict | None]:
    """按 plan_id 或 active 指针加载计划，返回 ``(plan, error)``。"""
    target = plan_id or _store.get_active()
    if not target:
        return None, _err(
            "no_active_plan",
            "当前没有学习计划",
            hint="先用 action=create_plan 建档，或 action=list_plans 查看已有计划",
        )
    ok, plan, reason = _store.load_plan(target)
    if not ok:
        return None, _err(
            "plan_unavailable",
            f"无法加载计划 {target}: {reason}",
            hint="计划可能已被删除或状态文件损坏，可用 create_plan 重新建档",
        )
    return plan, None


# --- action 实现 ------------------------------------------------------------


def _act_create_plan(payload: dict) -> dict:
    goal = _normalize_goal(payload.get("goal"))
    if not goal["title"]:
        return _err("invalid_goal", "goal.title 不能为空", hint='至少提供 {"title": "学习目标"}')

    kps = _normalize_kps(payload.get("knowledge_points"))
    if not kps:
        return _err(
            "empty_plan",
            "knowledge_points 不能为空",
            hint="每个知识点至少提供 title；建议同时给出 acceptance（怎样才算学会）",
        )

    plan_id = str(payload.get("plan_id") or "").strip() or _store.new_plan_id()
    now = _store.now_iso()
    plan = {
        "schema_version": _store.SCHEMA_VERSION,
        "plan_id": plan_id,
        "created_at": now,
        "updated_at": now,
        "goal": goal,
        "config": _mastery.normalize_config(payload.get("config")),
        "current_kp_id": kps[0]["id"],
        "knowledge_points": kps,
        "history": [],
    }
    _history(plan, "plan_created", total=len(kps))
    _store.save_plan(plan)
    _store.set_active(plan_id)

    return _ok(
        plan_id=plan_id,
        total=len(kps),
        config=plan["config"],
        current_kp=_kp_brief(kps[0]),
        progress=_progress(plan),
    )


def _act_load_plan(payload: dict) -> dict:
    plan, error = _load_or_error(payload.get("plan_id"))
    if error:
        return error
    assert plan is not None
    _store.set_active(plan["plan_id"])
    return _ok(
        plan_id=plan["plan_id"],
        goal=plan["goal"],
        config=plan["config"],
        current_kp=_kp_brief(_current_kp(plan)),
        progress=_progress(plan),
    )


def _act_list_plans(_payload: dict) -> dict:
    return _ok(plans=_store.list_plans(), active_plan_id=_store.get_active())


def _act_set_active(payload: dict) -> dict:
    plan_id = str(payload.get("plan_id") or "").strip()
    if not plan_id:
        return _err("missing_plan_id", "需要提供 plan_id", hint="用 action=list_plans 查看可用计划")
    ok, plan, reason = _store.load_plan(plan_id)
    if not ok:
        return _err("plan_unavailable", f"无法加载计划 {plan_id}: {reason}")
    assert plan is not None
    _store.set_active(plan_id)
    return _ok(plan_id=plan_id, goal=plan["goal"], current_kp=_kp_brief(_current_kp(plan)))


def _act_get_current(payload: dict) -> dict:
    plan, error = _load_or_error(payload.get("plan_id"))
    if error:
        return error
    assert plan is not None

    kp = _find_kp(plan, payload.get("kp_id")) or _current_kp(plan)
    if kp is None:
        return _ok(all_done=True, plan_id=plan["plan_id"], progress=_progress(plan))

    angles_used = _angles_used(kp)
    return _ok(
        plan_id=plan["plan_id"],
        goal=plan["goal"],
        config=plan["config"],
        kp=_kp_brief(kp),
        angles_tried=angles_used,
        suggested_angle=_mastery.next_angle(angles_used),
        attempt_rounds=len(kp.get("attempts") or []),
        progress=_progress(plan),
    )


def _act_record_teach(payload: dict) -> dict:
    plan, error = _load_or_error(payload.get("plan_id"))
    if error:
        return error
    assert plan is not None

    kp = _find_kp(plan, payload.get("kp_id")) or _current_kp(plan)
    if kp is None:
        return _err("no_knowledge_point", "没有可记录的知识点")

    angle = str(payload.get("angle") or "").strip()
    if angle not in _mastery.TEACH_ANGLES:
        return _err(
            "invalid_angle",
            f"未知讲解角度: {angle!r}",
            hint="可用角度: " + ", ".join(_mastery.TEACH_ANGLES),
        )

    tried = kp.setdefault("teach_angles_tried", [])
    if angle not in tried:
        tried.append(angle)
    if kp["status"] == STATUS_PENDING:
        kp["status"] = STATUS_LEARNING

    _history(plan, "taught", kp_id=kp["id"], angle=angle)
    _store.save_plan(plan)

    used = _angles_used(kp)
    return _ok(
        kp_id=kp["id"],
        angles_tried=used,
        next_angle=_mastery.next_angle(used),
        progress=_progress(plan),
    )


def _act_submit_answers(payload: dict) -> dict:
    """核心 action：判分 → 掌握度 → 达标判定 → 下一步动作。"""
    plan, error = _load_or_error(payload.get("plan_id"))
    if error:
        return error
    assert plan is not None

    kp = _find_kp(plan, payload.get("kp_id")) or _current_kp(plan)
    if kp is None:
        return _err("no_knowledge_point", "没有可判定的知识点")

    if kp.get("status") == STATUS_MASTERED:
        return _err(
            "already_mastered",
            f"知识点 {kp['id']} 已达标（掌握度 {kp.get('mastery')}）",
            hint="调用 action=advance 推进到下一个知识点",
        )

    items = [i for i in (payload.get("items") or []) if isinstance(i, dict)]
    if not items:
        return _err(
            "empty_answers",
            "items 不能为空",
            hint="每项至少含 type / user_answer；客观题附 answer_key，主观题附 rubric_score",
        )

    config = plan.get("config") or {}
    round_no = len(kp.get("attempts") or []) + 1
    result = _mastery.evaluate_attempt(
        items,
        round_no=round_no,
        config=config,
        angles_tried=_angles_used(kp),
        reteach_rounds=kp.get("reteach_rounds", 0),
        split_depth=kp.get("split_depth", 0),
    )

    weak_floor = config.get("weak_tag_floor", 0.6)
    attempt = {
        "ts": _store.now_iso(),
        "round": round_no,
        "items": result["items"],
        "weighted_accuracy": result["weighted_accuracy"],
        "penalty": result["penalty"],
        "mastery": result["mastery"],
        "verdict": result["verdict"],
        "action": result["action"],
    }
    kp.setdefault("attempts", []).append(attempt)
    kp["mastery"] = result["mastery"]
    if kp["status"] == STATUS_PENDING:
        kp["status"] = STATUS_LEARNING

    # 错题本：得分率低于阈值的题逐条留档
    wrong_now: list[dict] = []
    for graded in result["items"]:
        if graded["score"] >= weak_floor:
            continue
        entry = {
            "round": round_no,
            "qid": graded.get("qid"),
            "type": graded.get("type"),
            "user_answer": graded.get("user_answer"),
            "answer_key": graded.get("answer_key"),
            "score": graded["score"],
            "tags": graded.get("tags") or [],
        }
        kp.setdefault("wrong_points", []).append(entry)
        wrong_now.append(entry)

    action = result["action"]
    if result["verdict"] == "pass":
        kp["status"] = STATUS_MASTERED
        _history(plan, "mastered", kp_id=kp["id"], mastery=result["mastery"])
    elif action == _mastery.ACTION_RETEACH:
        kp["reteach_rounds"] = kp.get("reteach_rounds", 0) + 1
        # 记下建议角度，保证下一轮换角度不依赖模型是否调用了 record_teach
        if result["suggested_angle"]:
            suggested = kp.setdefault("angles_suggested", [])
            if result["suggested_angle"] not in suggested:
                suggested.append(result["suggested_angle"])
        _history(
            plan,
            "reteach",
            kp_id=kp["id"],
            angle=result["suggested_angle"],
            mastery=result["mastery"],
        )
    elif action == _mastery.ACTION_SPLIT:
        _history(plan, "split_suggested", kp_id=kp["id"], mastery=result["mastery"])
    else:  # ACTION_SKIP
        kp["status"] = STATUS_SKIPPED
        _history(plan, "skipped", kp_id=kp["id"], mastery=result["mastery"])

    _store.save_plan(plan)

    return _ok(
        plan_id=plan["plan_id"],
        kp_id=kp["id"],
        round=round_no,
        mastery=result["mastery"],
        threshold=result["threshold"],
        weighted_accuracy=result["weighted_accuracy"],
        heavy_avg=result["heavy_avg"],
        penalty=result["penalty"],
        verdict=result["verdict"],
        action=action,
        suggested_angle=result["suggested_angle"],
        weak_tags=result["weak_tags"],
        fail_reasons=result["fail_reasons"],
        wrong_now=wrong_now,
        kp_status=kp["status"],
        reteach_rounds=kp.get("reteach_rounds", 0),
        split_depth=kp.get("split_depth", 0),
        max_reteach_rounds=config.get("max_reteach_rounds", 3),
        max_split_depth=config.get("max_split_depth", 2),
        progress=_progress(plan),
    )


def _act_advance(payload: dict) -> dict:
    """推进到下一个知识点。强校验：未达标一律拒绝。"""
    plan, error = _load_or_error(payload.get("plan_id"))
    if error:
        return error
    assert plan is not None

    current = _current_kp(plan)
    if current is None:
        return _ok(all_done=True, plan_id=plan["plan_id"], progress=_progress(plan))

    if current.get("status") not in ADVANCEABLE:
        return _err(
            "not_mastered",
            f"知识点 {current['id']}（{current['title']}）尚未达标，当前状态 {current.get('status')}、掌握度 {current.get('mastery')}",
            hint="先通过验证（submit_answers 返回 verdict=pass），或按返回的 action 重讲 / 拆解",
        )

    nxt = _next_pending(plan, after_order=current.get("order"))
    if nxt is None:
        plan["current_kp_id"] = None
        _history(plan, "all_completed")
        _store.save_plan(plan)
        return _ok(all_done=True, plan_id=plan["plan_id"], progress=_progress(plan))

    plan["current_kp_id"] = nxt["id"]
    _history(plan, "advanced", from_kp=current["id"], to_kp=nxt["id"])
    _store.save_plan(plan)

    return _ok(
        all_done=False,
        plan_id=plan["plan_id"],
        previous_kp=_kp_brief(current),
        next_kp=_kp_brief(nxt),
        progress=_progress(plan),
    )


def _act_split_kp(payload: dict) -> dict:
    """把知识点降级拆解为更小颗粒度的子知识点（见 ADR-0003）。"""
    plan, error = _load_or_error(payload.get("plan_id"))
    if error:
        return error
    assert plan is not None

    kp = _find_kp(plan, payload.get("kp_id")) or _current_kp(plan)
    if kp is None:
        return _err("no_knowledge_point", "没有可拆解的知识点")

    max_depth = (plan.get("config") or {}).get("max_split_depth", 2)
    if kp.get("split_depth", 0) >= max_depth:
        return _err(
            "split_depth_exceeded",
            f"知识点 {kp['id']} 已拆解 {kp.get('split_depth')} 层，达到上限 {max_depth}",
            hint="该点已标记为薄弱点，调用 action=advance 跳过并继续后面的内容",
        )

    subs = _normalize_kps(payload.get("sub_points"))
    if len(subs) < 2:
        return _err(
            "invalid_sub_points",
            "至少需要 2 个子知识点",
            hint="提供 sub_points 数组，每项含 title 与 acceptance",
        )

    parent_order = kp.get("order", 1)
    for index, sub in enumerate(subs, start=1):
        sub["id"] = f"{kp['id']}-{index}"
        sub["parent_id"] = kp["id"]
        sub["split_depth"] = kp.get("split_depth", 0) + 1
        sub["order"] = parent_order + index

    kp["status"] = STATUS_SPLIT
    kp["mastery"] = 0.0

    kps = plan["knowledge_points"]
    position = kps.index(kp)
    kps[position + 1 : position + 1] = subs
    for index, item in enumerate(kps, start=1):
        item["order"] = index

    plan["current_kp_id"] = subs[0]["id"]
    _history(plan, "split", kp_id=kp["id"], into=[s["id"] for s in subs])
    _store.save_plan(plan)

    return _ok(
        plan_id=plan["plan_id"],
        parent_kp=_kp_brief(kp),
        new_kp_ids=[s["id"] for s in subs],
        current_kp=_kp_brief(subs[0]),
        progress=_progress(plan),
    )


def _act_get_summary(payload: dict) -> dict:
    plan, error = _load_or_error(payload.get("plan_id"))
    if error:
        return error
    assert plan is not None

    kps = plan.get("knowledge_points", [])
    table = [
        {
            "id": kp.get("id"),
            "order": kp.get("order"),
            "title": kp.get("title"),
            "difficulty": kp.get("difficulty"),
            "status": kp.get("status"),
            "mastery": kp.get("mastery", 0.0),
            "reteach_rounds": kp.get("reteach_rounds", 0),
            "split_depth": kp.get("split_depth", 0),
        }
        for kp in kps
    ]

    wrong_book: list[dict] = []
    tag_counter: dict[str, int] = {}
    for kp in kps:
        for entry in kp.get("wrong_points") or []:
            wrong_book.append({"kp_id": kp["id"], "kp_title": kp.get("title"), **entry})
            for tag in entry.get("tags") or []:
                tag_counter[str(tag)] = tag_counter.get(str(tag), 0) + 1

    weak_top = [
        {"tag": tag, "count": count}
        for tag, count in sorted(tag_counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ][:10]

    return _ok(
        plan_id=plan["plan_id"],
        goal=plan["goal"],
        progress=_progress(plan),
        current_kp=_kp_brief(_current_kp(plan)),
        kp_table=table,
        wrong_book=wrong_book,
        weak_top=weak_top,
        created_at=plan.get("created_at"),
        updated_at=plan.get("updated_at"),
    )


def _act_reset(payload: dict) -> dict:
    if payload.get("confirm") is not True:
        return _err(
            "confirm_required",
            "重置会永久删除学习记录，需要显式确认",
            hint="请用户确认后以 confirm=true 重新调用",
        )
    plan_id = str(payload.get("plan_id") or _store.get_active() or "").strip()
    if not plan_id:
        return _err("no_active_plan", "没有可重置的计划")
    deleted = _store.delete_plan(plan_id)
    return _ok(plan_id=plan_id, deleted=deleted)


_HANDLERS = {
    "create_plan": _act_create_plan,
    "load_plan": _act_load_plan,
    "list_plans": _act_list_plans,
    "set_active": _act_set_active,
    "get_current": _act_get_current,
    "record_teach": _act_record_teach,
    "submit_answers": _act_submit_answers,
    "advance": _act_advance,
    "split_kp": _act_split_kp,
    "get_summary": _act_get_summary,
    "reset": _act_reset,
}


def _dispatch(action: str, payload: dict | None = None) -> dict:
    """纯逻辑分发入口，供单测直接调用。"""
    name = str(action or "").strip()
    handler = _HANDLERS.get(name)
    if handler is None:
        return _err(
            "unknown_action",
            f"未知 action: {name!r}",
            hint="可用 action: " + ", ".join(ACTIONS),
        )
    return handler(payload or {})


# --- 平台工具注册 -----------------------------------------------------------


@tool(
    id="tool:learning_state",
    version="0.1.0",
    description=(
        "学习计划与进度的状态机：建档、取当前知识点、记录讲解、提交作答并判定是否掌握、"
        "推进/拆解知识点、生成学习报告。"
        "【重要】判断学习者是否掌握必须调用本工具的 submit_answers，"
        "严禁自行宣称学习者已掌握。"
        "【输出纪律】正文只写面向学习者的内容；调用工具失败时静默重试或换方式完成，"
        "绝不把思考过程、工具调用叙述或报错原文写进正文。"
    ),
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(ACTIONS),
                "description": (
                    "create_plan=建档；load_plan=加载计划；get_current=取当前知识点；"
                    "record_teach=记录一次讲解；submit_answers=提交作答并判定掌握度；"
                    "advance=推进到下一知识点（仅当前点已达标时可用）；"
                    "split_kp=把知识点拆成更小的子点；get_summary=学习报告数据；"
                    "list_plans/set_active=多计划管理；reset=删除计划（需 confirm）"
                ),
            },
            "plan_id": {
                "type": "string",
                "description": "计划 ID；缺省时使用当前活跃计划",
            },
            "goal": {
                "type": "object",
                "description": "create_plan 用。学习目标：title(必填)、deadline、daily_minutes、current_level(beginner/intermediate/advanced)、notes",
            },
            "knowledge_points": {
                "type": "array",
                "items": {"type": "object"},
                "description": "create_plan 用。知识点数组，每项含 title、acceptance(怎样才算学会)、difficulty(1-5)、est_minutes",
            },
            "config": {
                "type": "object",
                "description": "create_plan 用。可选覆盖：mastery_threshold(默认0.8)、heavy_type_floor(0.6)、heavy_types、max_reteach_rounds(3)、max_split_depth(2)",
            },
            "kp_id": {
                "type": "string",
                "description": "目标知识点 ID；缺省时使用当前知识点",
            },
            "angle": {
                "type": "string",
                "enum": list(_mastery.TEACH_ANGLES),
                "description": "record_teach 用。本次讲解采用的角度：intuition/visual/example/contrast/formal/socratic",
            },
            "items": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "submit_answers 用。本轮作答明细，每项含："
                    "qid、type(single/multi/short/apply)、user_answer、"
                    "answer_key(客观题标准答案)、rubric_score(主观题 0~1 得分，由模型依据评分要点给出)、tags(关联子概念)"
                ),
            },
            "sub_points": {
                "type": "array",
                "items": {"type": "object"},
                "description": "split_kp 用。拆解出的子知识点数组（至少 2 个），每项含 title 与 acceptance",
            },
            "confirm": {
                "type": "boolean",
                "description": "reset 用。必须显式传 true 才会执行删除",
            },
        },
        "required": ["action"],
    },
)
def learning_state(
    action: str,
    plan_id: str | None = None,
    goal: dict | None = None,
    knowledge_points: list | None = None,
    config: dict | None = None,
    kp_id: str | None = None,
    angle: str | None = None,
    items: list | None = None,
    sub_points: list | None = None,
    confirm: bool = False,
) -> str:
    """学习状态机入口。返回 JSON 字符串。"""
    result = _dispatch(
        action,
        {
            "plan_id": plan_id,
            "goal": goal,
            "knowledge_points": knowledge_points,
            "config": config,
            "kp_id": kp_id,
            "angle": angle,
            "items": items,
            "sub_points": sub_points,
            "confirm": confirm,
        },
    )
    return json.dumps(result, ensure_ascii=False)
