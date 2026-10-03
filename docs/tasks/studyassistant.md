# 学习助手 (studyassistant) — 任务拆解

| 项 | 内容 |
|---|---|
| 版本 | 0.1.0 |
| 状态 | 待确认 |
| 日期 | 2026-09-28 |
| 阶段 | 阶段 3 · 任务拆解 |
| 关联 | [需求](../requirements/studyassistant.md) · [设计](../design/studyassistant.md) |

---

## 依赖关系

```
T1 工程骨架
 └─> T2 掌握度算法 (_mastery.py) ──┐
      └─> T3 持久化 (_store.py) ───┼─> T4 状态工具 (learning_state.py)
                                   │        └─> T7 单测(工具层)
 T5 技能: study_plan ──────────────┤
 T6 技能: teach / quiz_generate /  │
         quiz_grade / study_report ┘
                                   └─> T8 test_cases.yaml
                                        └─> T9 自闭环验证 (pytest/validate/test)
                                             └─> T10 文档同步
```

## 任务清单

| ID | 任务 | 产出物 | 依赖 | 优先级 | 状态 | 覆盖验收标准 |
|---|---|---|---|---|---|---|
| **T1** | 清理骨架 demo，配置 `plugin.yaml`（depends_on 五项 + 自有 skills/tools 声明）与 `pyproject.toml` | `plugin.yaml`, `pyproject.toml` | — | P0 | ✅ demo 已删除 | — |
| **T2** | 实现掌握度纯函数：题型权重、加权得分率、高权重门槛、尝试惩罚、达标判定、动作决策、角度轮换、薄弱标签聚合 | `tools/_mastery.py` | T1 | P0 | ✅ | AC-5, AC-6, AC-7, AC-8 |
| **T3** | 实现 JSON 持久化层：路径解析（env 覆盖）、原子写、读取降级、计划 CRUD、active 指针 | `tools/_store.py` | T1 | P0 | ✅ | AC-2, 非功能·容错 |
| **T4** | 实现 `@tool(id="tool:learning_state")`：11 个 action 分发，封装 T2+T3；`advance` 强校验达标 | `tools/learning_state.py` | T2, T3 | P0 | ✅ | AC-2, AC-3, AC-4, AC-12 |
| **T5** | 技能 `skill:study_plan`：知识树拆解 Prompt（含 acceptance、颗粒度约束、顺序依赖） | `skills/study_plan.py` | T1 | P0 | ✅ | AC-1 |
| **T6** | 技能 `skill:study_teach`：六角度分层讲解 Prompt + `need_diagram`/`diagram_prompt` 输出 | `skills/teach.py` | T1 | P0 | ✅ | AC-9, AC-10 |
| **T7** | 技能 `skill:study_quiz_generate`：出题 Prompt，强制输出 `answer_key`/`rubric`/`tags` | `skills/quiz_generate.py` | T1 | P0 | ✅ | AC-4, AC-12 |
| **T8** | 技能 `skill:study_quiz_grade`：主观题判分与错因诊断，只给 `0~1` 分不做判定 | `skills/quiz_grade.py` | T1 | P0 | ✅ | AC-8 |
| **T9** | 技能 `skill:study_report`：学习报告 Markdown（进度/掌握度/薄弱 Top-N/下一步） | `skills/study_report.py` | T1 | P1 | ✅ | AC-13 |
| **T10** | 单测：`test_mastery.py`（边界/门槛/惩罚/轮换/收敛）、`test_store.py`（原子写/损坏降级/目录隔离）、`test_learning_state.py`（状态机全流程） | `test/test_*.py` | T4 | P0 | ✅ 83 passed | AC-2~AC-8, AC-12 |
| **T11** | 自动化用例：`test/test_cases.yaml` 覆盖建档、学习、验证、未过重讲主路径 | `test/test_cases.yaml` | T4, T5~T9 | P0 | ✅ | AC-1~AC-14 |
| **T12** | 自闭环验证：`pytest` → `agentplatform validate .` → `agentplatform test .`，按结构化报错原地修正 | 全绿 | T10, T11 | P0 | ✅ pytest 83 通过 / validate ok / test ok | — |
| **T13** | 文档同步：回填实现与设计的差异，更新 `plugin.yaml` 版本与描述 | `docs/**` | T12 | P1 | ✅ | — |
| **T14** | 本地冒烟（人肉测试）：走一遍完整闭环，重点验证「未达标换角度重讲」与「多次后拆解」 | 冒烟结论 | T12 | P0 | ⏳ 待用户参与 | AC-9~AC-14 |
| **T15** | 发布：用户确认后 `agentplatform deploy .` | 已部署 | T14 | P1 | ⏳ 待用户确认 | — |

## 实现与设计的偏差记录

| 偏差 | 原因 | 处理 |
|---|---|---|
| 新增 `action=skip` 与 `status=skipped` | 拆解层数触顶后必须能推进，否则计划卡死 | 已回填设计 §3⑧、§2.2 与需求 AC-3、AC-8 |
| 新增 `angles_suggested` 字段 | 模型漏调 `record_teach` 时建议角度不轮换，「换角度重讲」会退化成重复同一讲法 | 已回填设计 §3 与风险表 |
| 技能 id 由 `skill:teach` 等改为 `skill:study_teach` 等 | 避免与平台/其他插件的通用名冲突 | 已回填设计 §5 |
| 新增 `conftest.py`；`skills/` 不加 `__init__.py` | 让 `test/` 能 import `tools` 包；`skills/` 无需被测试导入 | 已回填设计 §8 |

## 里程碑

| 里程碑 | 包含任务 | 完成标志 |
|---|---|---|
| **M1 确定性内核** | T1–T4, T10 | `pytest` 全绿；状态机与掌握度算法可独立验证 |
| **M2 智能体闭环** | T5–T9, T11 | `agentplatform validate .` 与 `agentplatform test .` 全绿 |
| **M3 可交付** | T12–T15 | 本地冒烟走通完整闭环，报告可导出，确认后发布 |

## 风险登记

| 风险 | 触发点 | 应对 |
|---|---|---|
| 插件运行时可写目录假设不成立 | T3/T12 | 落用户主目录 + env 覆盖；验证失败则改用平台约定的数据目录 |
| `depends_on` 版本约束校验不通过 | T1/T12 | 按 `agentplatform context` 实际版本调整 `^` 约束 |
| 技能 Prompt 未被模型稳定遵守 | T14 | 强约束 + 讲解末尾固定抛检验问题；必要时在工具侧加校验 |
| `tool:image_gen` 在会话中不可用 | T6/T14 | 设计为软依赖，`need_diagram` 失败降级为文本图示 |
