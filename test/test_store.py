"""持久化层单测：目录隔离、原子写、损坏降级、计划 CRUD。"""
import json

import pytest

from tools import _store as S


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv(S.DATA_DIR_ENV, str(root))
    return root


def _plan(plan_id="pln-test-0001"):
    return {
        "schema_version": S.SCHEMA_VERSION,
        "plan_id": plan_id,
        "created_at": S.now_iso(),
        "updated_at": S.now_iso(),
        "goal": {"title": "测试目标"},
        "knowledge_points": [],
        "history": [],
    }


# --- 目录解析 ---------------------------------------------------------------


def test_data_root_respects_env_override(isolated_root):
    assert S.data_root() == isolated_root
    assert S.plans_dir() == isolated_root / "plans"


def test_data_root_defaults_to_home(monkeypatch):
    monkeypatch.delenv(S.DATA_DIR_ENV, raising=False)
    assert S.data_root() == S.Path.home() / ".studyassistant"


def test_data_root_ignores_blank_env(monkeypatch):
    monkeypatch.setenv(S.DATA_DIR_ENV, "   ")
    assert S.data_root() == S.Path.home() / ".studyassistant"


# --- 读写 -------------------------------------------------------------------


def test_atomic_write_and_read_roundtrip(isolated_root):
    target = isolated_root / "plans" / "x.json"
    payload = {"中文键": "值", "nested": [1, 2, 3]}
    S.atomic_write_json(target, payload)

    ok, data, reason = S.read_json(target)
    assert ok is True
    assert reason is None
    assert data == payload


def test_atomic_write_leaves_no_temp_files(isolated_root):
    target = isolated_root / "plans" / "x.json"
    S.atomic_write_json(target, {"a": 1})
    leftovers = [p.name for p in target.parent.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_read_missing_file_degrades(isolated_root):
    ok, data, reason = S.read_json(isolated_root / "nope.json")
    assert ok is False
    assert data is None
    assert "不存在" in reason


def test_read_corrupted_file_degrades(isolated_root):
    target = isolated_root / "plans" / "broken.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{ this is not json", encoding="utf-8")

    ok, data, reason = S.read_json(target)
    assert ok is False
    assert data is None
    assert "损坏" in reason


# --- 计划 CRUD --------------------------------------------------------------


def test_save_and_load_plan():
    plan = _plan()
    S.save_plan(plan)
    ok, loaded, reason = S.load_plan(plan["plan_id"])
    assert ok is True
    assert reason is None
    assert loaded["plan_id"] == plan["plan_id"]
    assert loaded["goal"]["title"] == "测试目标"


def test_save_plan_updates_timestamp():
    plan = _plan()
    plan["updated_at"] = "1970-01-01T00:00:00+00:00"
    S.save_plan(plan)
    _, loaded, _ = S.load_plan(plan["plan_id"])
    assert loaded["updated_at"] != "1970-01-01T00:00:00+00:00"


def test_load_plan_rejects_incompatible_schema_version():
    plan = _plan("pln-old")
    plan["schema_version"] = "0.0"
    S.atomic_write_json(S.plan_path("pln-old"), plan)

    ok, data, reason = S.load_plan("pln-old")
    assert ok is False
    assert data is None
    assert "版本不兼容" in reason


def test_load_plan_rejects_non_object_payload():
    S.atomic_write_json(S.plan_path("pln-arr"), [1, 2, 3])
    ok, data, reason = S.load_plan("pln-arr")
    assert ok is False
    assert "不是对象" in reason


def test_list_plans_is_sorted_by_updated_at():
    for index, stamp in enumerate(["2026-01-01T00:00:00+08:00", "2026-03-01T00:00:00+08:00"]):
        plan = _plan(f"pln-{index}")
        plan["updated_at"] = stamp
        S.atomic_write_json(S.plan_path(plan["plan_id"]), plan)

    plans = S.list_plans()
    assert [p["plan_id"] for p in plans] == ["pln-1", "pln-0"]


def test_list_plans_skips_corrupted_files():
    S.save_plan(_plan("pln-good"))
    broken = S.plan_path("pln-bad")
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("garbage", encoding="utf-8")

    assert [p["plan_id"] for p in S.list_plans()] == ["pln-good"]


def test_list_plans_empty_when_no_directory(isolated_root):
    assert S.list_plans() == []


# --- active 指针 ------------------------------------------------------------


def test_active_pointer_roundtrip():
    assert S.get_active() is None
    S.set_active("pln-a")
    assert S.get_active() == "pln-a"


def test_get_active_degrades_on_corruption():
    S.active_file().parent.mkdir(parents=True, exist_ok=True)
    S.active_file().write_text("not json", encoding="utf-8")
    assert S.get_active() is None


def test_delete_plan_clears_active_pointer():
    S.save_plan(_plan("pln-del"))
    S.set_active("pln-del")

    assert S.delete_plan("pln-del") is True
    assert S.get_active() is None
    assert S.load_plan("pln-del")[0] is False


def test_delete_missing_plan_returns_false():
    assert S.delete_plan("pln-nope") is False


def test_active_pointer_preserved_when_deleting_other_plan():
    S.save_plan(_plan("pln-a"))
    S.save_plan(_plan("pln-b"))
    S.set_active("pln-a")

    S.delete_plan("pln-b")
    assert S.get_active() == "pln-a"


# --- 原子性 -----------------------------------------------------------------


def test_atomic_write_replaces_existing_content():
    target = S.plan_path("pln-x")
    S.atomic_write_json(target, {"v": 1})
    S.atomic_write_json(target, {"v": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}
