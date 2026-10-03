"""学习状态的 JSON 持久化层（纯 IO，无业务逻辑）。

存储布局::

    $STUDYASSISTANT_DATA_DIR/          # 默认 ~/.studyassistant
    ├── active.json                    # {"plan_id": "..."} 指向当前活跃计划
    └── plans/
        └── <plan_id>.json             # 单个学习计划的完整状态

设计约束见 docs/adr/0001-progress-persistence.md：

- 默认落**用户主目录**而非插件工程目录，避免污染代码仓库。
- 写入采用**临时文件 + 原子替换**，避免中途崩溃导致状态文件损坏。
- 读取失败（不存在 / JSON 损坏 / 版本不识别）**降级返回**，不抛异常 ——
  由上层提示用户重建，而不是让整个会话崩掉。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: 状态根目录的环境变量覆盖点（单测注入 tmp 目录用）
DATA_DIR_ENV = "STUDYASSISTANT_DATA_DIR"

#: 状态文件 schema 版本，为将来迁移预留
SCHEMA_VERSION = "1.0"


# --- 路径解析 ---------------------------------------------------------------


def data_root() -> Path:
    """状态根目录。``STUDYASSISTANT_DATA_DIR`` 优先，否则 ``~/.studyassistant``。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".studyassistant"


def plans_dir() -> Path:
    return data_root() / "plans"


def active_file() -> Path:
    return data_root() / "active.json"


def plan_path(plan_id: str) -> Path:
    return plans_dir() / f"{plan_id}.json"


# --- 时间与 ID --------------------------------------------------------------


def now_iso() -> str:
    """本地时区的 ISO8601 时间戳。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_plan_id() -> str:
    return f"pln-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


# --- 底层读写 ---------------------------------------------------------------


def atomic_write_json(path: Path, payload: Any) -> None:
    """原子写入 JSON：先写同目录临时文件，fsync 后 replace 覆盖目标。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> tuple[bool, Any, str | None]:
    """读取 JSON，返回 ``(ok, data, reason)``。失败不抛异常。"""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return True, json.load(handle), None
    except FileNotFoundError:
        return False, None, f"文件不存在: {path}"
    except json.JSONDecodeError as exc:
        return False, None, f"状态文件已损坏（JSON 解析失败）: {exc}"
    except OSError as exc:
        return False, None, f"读取失败: {exc}"


# --- 计划 CRUD --------------------------------------------------------------


def save_plan(plan: dict) -> None:
    plan["updated_at"] = now_iso()
    atomic_write_json(plan_path(plan["plan_id"]), plan)


def load_plan(plan_id: str) -> tuple[bool, dict | None, str | None]:
    """加载计划，校验 schema 版本。"""
    ok, data, reason = read_json(plan_path(plan_id))
    if not ok:
        return False, None, reason
    if not isinstance(data, dict):
        return False, None, "状态文件内容不是对象"
    version = str(data.get("schema_version") or "")
    if version != SCHEMA_VERSION:
        return False, None, f"状态文件版本不兼容（文件 {version or '未知'}，期望 {SCHEMA_VERSION}）"
    return True, data, None


def list_plans() -> list[dict]:
    """列出所有计划的摘要，按更新时间倒序。"""
    directory = plans_dir()
    if not directory.is_dir():
        return []
    summaries: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        ok, data, _ = read_json(path)
        if not ok or not isinstance(data, dict):
            continue
        goal = data.get("goal") if isinstance(data.get("goal"), dict) else {}
        summaries.append(
            {
                "plan_id": data.get("plan_id") or path.stem,
                "title": goal.get("title") or "(未命名)",
                "updated_at": data.get("updated_at"),
                "created_at": data.get("created_at"),
            }
        )
    summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return summaries


def set_active(plan_id: str) -> None:
    atomic_write_json(active_file(), {"plan_id": plan_id})


def get_active() -> str | None:
    ok, data, _ = read_json(active_file())
    if not ok or not isinstance(data, dict):
        return None
    plan_id = data.get("plan_id")
    return str(plan_id) if plan_id else None


def delete_plan(plan_id: str) -> bool:
    """删除计划文件；若删除的是活跃计划，同时清掉 active 指针。"""
    path = plan_path(plan_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    if get_active() == plan_id:
        try:
            active_file().unlink()
        except OSError:
            pass
    return True
