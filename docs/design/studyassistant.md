# 学习助手 (studyassistant) — 技术设计

| 项 | 内容 |
|---|---|
| 版本 | 0.1.0 |
| 状态 | 待确认 |
| 日期 | 2026-09-28 |
| 阶段 | 阶段 2 · 设计 |
| 关联需求 | [requirements/studyassistant.md](../requirements/studyassistant.md) |

---

## 1. 总体架构

插件严格遵循平台分层：**Prompt 思考链进 `skills/`，确定性计算进 `tools/`，平台公共能力走 `depends_on`。**

```
┌──────────────────────────────────────────────────────────────┐
│  平台运行时 (模型 + output_block 富交互 + 公共能力)            │
└───────┬──────────────────────────────────────────────────────┘
        │
        │  ┌──────────── 本插件 (studyassistant) ────────────┐
        │  │                                                  │
        └──┼─> skills/  (Prompt 思考链 · 非确定性)             │
           │     study_plan        知识树拆解                  │
           │     teach             多角度分层讲解              │
           │     quiz_generate     出题(含答案/评分要点)       │
           │     quiz_grade        主观题判分与错因诊断        │
           │     study_report      阶段学习报告                │
           │                                                  │
           │   tools/   (确定性 Python · 可单测)              │
           │     learning_state    计划/进度/错题本 状态机      │
           │       └── _mastery    掌握度算法(纯函数)         │
           │       └── _store      JSON 持久化(纯 IO)         │
           └──────────────────────────────────────────────────┘
        │
        └─> depends_on 平台公共能力
              tool:pdf_parse          PDF 教材解析
              tool:kb_search          私有知识库检索
              tool:image_gen          教学示意图生成
              skill:summarize         长材料摘要
              skill:structured_output 结构化抽取
              (默认可用: tool:web_search / tool:memory / tool:workbench_todo)
```

## 2. 状态模型

### 2.1 存储布局

```
$STUDYASSISTANT_DATA_DIR/            # 默认 ~/.studyassistant
├── active.json                      # {"plan_id": "..."} 指向当前活跃计划
└── plans/
    └── <plan_id>.json               # 单个学习计划的完整状态
```

- `STUDYASSISTANT_DATA_DIR` 环境变量可覆盖根目录（单测注入 tmp_path 用）。
- 写入采用 **临时文件 + 原子替换**，避免中途崩溃导致文件损坏。
- 读取失败（不存在/JSON 损坏/版本不识别）→ 返回 `{"ok": false, "reason": ...}`，由助手提示重建，**不抛异常**。

### 2.2 计划状态 Schema

```jsonc
{
  "schema_version": "1.0",
  "plan_id": "pln-20260928-a1b2c3",
  "created_at": "2026-09-28T10:00:00+08:00",
  "updated_at": "2026-09-28T10:35:00+08:00",

  "goal": {
    "title": "掌握线性代数核心概念",
    "deadline": "2026-11-30",
    "daily_minutes": 40,
    "current_level": "beginner",        // beginner | intermediate | advanced
    "notes": "考研数学一，基础薄弱"
  },

  "config": {
    "mastery_threshold": 0.8,           // 达标线
    "heavy_type_floor": 0.6,            // 高权重题型得分率门槛
    "heavy_types": ["short", "apply"],  // 视为高权重的题型
    "max_reteach_rounds": 3,            // 超过则建议拆解
    "max_split_depth": 2                // 拆解层数上限，防无限下钻
  },

  "current_kp_id": "kp-002",

  "knowledge_points": [
    {
      "id": "kp-001",
      "title": "向量与线性组合的直觉",
      "parent_id": null,
      "order": 1,
      "difficulty": 1,                  // 1~5
      "est_minutes": 25,
      "acceptance": [                   // 「怎样才算学会」
        "能用自己的话解释线性组合的几何含义",
        "能判断给定向量是否在张成空间内"
      ],
      "status": "mastered",             // pending | learning | mastered | skipped | split
      "mastery": 0.87,                  // 最近一次计算的掌握度
      "teach_angles_tried": ["intuition", "example"],   // 实际讲过的角度
      "angles_suggested": ["visual"],   // 工具建议过的角度（防止模型漏调 record_teach 导致重复讲法）
      "reteach_rounds": 1,
      "split_depth": 0,
      "attempts": [ AttemptRecord, ... ],
      "wrong_points": ["张成空间与列空间混淆"]
    }
  ],

  "history": [                          // 关键事件流，用于报告与复盘
    {"ts": "...", "event": "plan_created"},
    {"ts": "...", "event": "taught",        "kp_id": "kp-001", "angle": "intuition"},
    {"ts": "...", "event": "attempt",       "kp_id": "kp-001", "verdict": "fail"},
    {"ts": "...", "event": "reteach",       "kp_id": "kp-001", "angle": "example"},
    {"ts": "...", "event": "mastered",      "kp_id": "kp-001"},
    {"ts": "...", "event": "split",         "kp_id": "kp-003", "into": ["kp-003a","kp-003b"]}
  ]
}
```

### 2.3 答题记录 AttemptRecord

```jsonc
{
  "ts": "2026-09-28T10:20:00+08:00",
  "round": 2,                           // 该知识点第几轮验证（1-based）
  "items": [
    {
      "qid": "q1",
      "type": "single",                 // single | multi | short | apply
      "user_answer": "B",               // 客观题：选项；主观题：作答文本
      "answer_key": "B",                // 客观题标准答案（多选为排序后字符串）
      "rubric_score": null,             // 主观题：模型依据评分要点给的分 0~1
      "correct": true,                  // 客观题由工具确定性判定
      "score": 1.0,                     // 本题得分率 0~1
      "tags": ["线性组合"]              // 关联子概念，用于错因聚合
    }
  ],
  "weighted_accuracy": 0.78,
  "penalty": 0.9,
  "mastery": 0.70,
  "verdict": "fail",                    // pass | fail
  "action": "reteach",                  // advance | reteach | split | skip
  "suggested_angle": "visual",
  "weak_tags": ["线性组合"],
  "fail_reasons": ["高权重题型得分率 0.5 低于门槛 0.6"]
}
```

## 3. 确定性掌握度算法

> 设计动机见 [ADR-0002](../adr/0002-deterministic-mastery-scoring.md)。**掌握度绝不由模型主观判定**，模型只负责给主观题打 0~1 的分（依据评分要点），聚合与达标判定全部是纯函数。

```
输入: items[]、round(本轮次)、config

① 题型基准权重
     single=1.0  multi=1.4  short=2.0  apply=2.5
   （题型可由 item.type 指定；显式 weight 优先）

② 每题得分率
     客观题: correct ? 1.0 : 0.0        ← 字符串比对，确定性
     主观题: clamp(rubric_score, 0, 1)

③ 加权得分率
     weighted_accuracy = Σ(wᵢ · scoreᵢ) / Σ(wᵢ)          (无题时 = 0)

④ 高权重题型门槛
     heavy = items where type ∈ config.heavy_types
     若 heavy 非空 且 avg(score of heavy) < heavy_type_floor
        → 门槛不通过，verdict 直接 = fail（防止靠选择题刷分）

⑤ 尝试次数惩罚
     penalty = max(0.7, 1 - 0.1 × (round - 1))
     （第 1 轮无惩罚，此后每轮 -0.1，下限 0.7）

⑥ 掌握度
     mastery = round(weighted_accuracy × penalty, 4)

⑦ 达标判定
     verdict = pass  ⟺  mastery ≥ mastery_threshold  且  门槛通过

⑧ 后续动作
     pass                              → action = "advance"
     fail 且 reteach_rounds < max      → action = "reteach"，suggested_angle = 首个未用过的角度
     fail 且 reteach_rounds ≥ max
          且 split_depth < max_depth    → action = "split"
     fail 且 split_depth ≥ max_depth   → action = "skip"（标记薄弱点，允许推进）

⑨ 薄弱子点
     weak_tags = 去重( items where scoreᵢ < 0.6 的 tags )
```

**角度轮换顺序**（`suggested_angle` 按序取第一个**未用过**的）：

```
intuition(直觉类比) → visual(图示) → example(实例推演)
→ contrast(对比辨析) → formal(严谨定义) → socratic(追问引导)
```

轮换耗尽后回到 `intuition`，但此时必然已触发 `split`。

**「未用过」= `teach_angles_tried` ∪ `angles_suggested`。** 后者是工具自己记下的「已建议过」的角度。
把建议也算进去，是为了让换角度重讲**不依赖模型是否老实调用了 `record_teach`** ——
否则模型一旦漏调，`next_angle` 会一直返回同一个角度，「换一种讲法」就退化成了「重复同一种讲法」，
正是 ADR-0003 要避免的失败模式。

**为什么这样设计**：分数可解释（能说清为什么是 0.70 而不是 0.75）、可单测（纯函数、无 IO）、抗刷分（高权重门槛 + 尝试惩罚）、且天然收敛（重讲上限 → 拆解 → 拆解上限）。

## 4. 工具规格：`tool:learning_state`

单一工具，`action` 分发。所有写操作自动维护 `updated_at` 与 `history`。

| action | 入参 | 出参（关键字段） | 说明 |
|---|---|---|---|
| `create_plan` | `goal{}`, `knowledge_points[]`, `config?` | `plan_id`, `total`, `first_kp_id` | 建计划并置为 active；`order` 缺省按数组序补齐 |
| `load_plan` | `plan_id?`（缺省用 active） | `plan_id`, `goal`, `progress{}`, `current_kp` | 会话开场「继续学上次的」 |
| `get_current` | `plan_id?` | `kp{}`, `angles_tried[]`, `reteach_rounds`, `split_depth` | 取当前待学知识点 |
| `record_teach` | `kp_id?`, `angle` | `angles_tried[]`, `next_angle` | 记录一次讲解 |
| `submit_answers` | `kp_id?`, `items[]` | `mastery`, `verdict`, `action`, `suggested_angle`, `weak_tags`, `fail_reasons`, `wrong_points` | **核心**：判分 + 掌握度 + 决策，一次返回 |
| `advance` | `plan_id?` | `next_kp{}` \| `all_done: true`, `progress{}` | 仅当前 kp 为 `mastered` 时允许 |
| `split_kp` | `kp_id?`, `sub_points[]` | `new_kp_ids[]` | 拆解为更小颗粒度子点，替换原点的位置 |
| `get_summary` | `plan_id?` | `progress{}`, `kp_table[]`, `wrong_book[]`, `weak_top[]` | 供报告与进度表使用 |
| `list_plans` | — | `plans[]` | 列出所有计划 |
| `set_active` / `reset` | `plan_id` / `confirm` | `ok` | 切换 / 重置（`reset` 需显式 `confirm=true`） |

**输出约定**：统一返回 JSON 字符串，含 `ok` 字段；失败时给 `reason` 与可执行的 `hint`（供模型自愈）。

## 5. 技能规格

| 技能 id | 输入 | 输出 | 关键 Prompt 约束 |
|---|---|---|---|
| `skill:study_intake` | `goal_hint?`, `learner_note?` | 招呼语 + `input.form` 摸底表单 | **仅学习者表达学习需求时调用一次**；纯问候不调（平台规范）；禁止罗列能力清单；字段固定为 目标/基础/每日时长/截止日/知识来源 |
| `skill:study_plan` | `goal`, `current_level`, `daily_minutes`, `deadline`, `domain`, `source_material?` | 知识树 JSON（含 acceptance） | 颗粒度：单点 ≤ 1 次学习时段可完成；必须可验证；顺序满足依赖 |
| `skill:study_teach` | `kp_title`, `acceptance`, `angle`, `difficulty`, `learner_level`, `weak_points?`, `source_material?` | 讲解正文 + `need_diagram` / `diagram_prompt` / `check_question` | **一次只讲一个点**；按 angle 切换讲法；末尾抛检验问题 |
| `skill:study_quiz_generate` | `kp_title`, `acceptance`, `difficulty`, `count`, `type_mix?`, `weak_points?`, `source_material?` | 题目 JSON（含 `answer_key` / `rubric` / `tags`） | 必须产出标准答案与评分要点；至少 1 道简答/应用题 |
| `skill:study_quiz_grade` | `kp_title`, `question`, `rubric`, `user_answer` | `rubric_score` + `diagnosis` + `missing_points` | 只给 0~1 分，**不做达标判定**（判定归工具） |
| `skill:study_report` | `summary`（工具输出）, `learner_note?` | Markdown 报告 | 含进度、掌握度分布、薄弱 Top-N、下一步建议 |

角度到讲解指令的映射（`intuition` / `visual` / `example` / `contrast` / `formal` / `socratic`）
由 `skills/teach.py` 的 `ANGLE_GUIDE` 确定性给出，键必须与 `_mastery.TEACH_ANGLES` 保持一致。

**技能与工具的分工红线**：技能**不得**自行宣称「你已掌握」——达标与否只能由 `tool:learning_state` 返回。

### 5.1 人设的承载方式（实测结论）

平台 `plugin.yaml` 的字段是固定的 9 个（`name` / `display_name` / `version` / `description` / `author` / `model` / `depends_on` / `skills` / `tools`），**没有专门的人设 / system prompt 字段**（4 个官方模板一致，且 validate 对未知字段宽容，不报错也不生效）。

实测验证：把 `plugin.yaml` 的 `description` 写成明确的人设指令后，开场问候**毫无变化**，模型依然罗列「我能做摘要、生成图片、检索资料」——
说明 **`plugin.yaml` 的 `description` 不会进入系统提示**。

真正生效的是**技能 / 工具的 `description`**（它们作为工具定义进入上下文）。因此：

- 人设必须写进 `skill:study_intake` 的 `description`，并且要**足够强制**（「开场必调」「严禁说我是智能助手」）。
- 只把指令写在技能的 `prompt` 里不够 —— `prompt` 只有被调用后才生效，而开场问题恰恰是「模型不调用」。
- 同理，`tool:learning_state` 的 `description` 里那句「判断是否掌握必须调用 submit_answers，严禁自行宣称」也是靠这个机制生效的。

结论：**在本平台，插件对模型行为的约束力来自工具/技能的 description，而不是 plugin.yaml。**

### 5.2 平台全局 UX 规范：禁止在问候阶段弹表单

实测中发现模型在推理里引述了一条平台全局规范：

> 「问候、闲聊、咨询、模糊意向阶段一律正常文本交流……禁止上来就弹表单」

（该规范由模型在推理中引述，我们无法独立核验原文，但模型的行为与之一致。）

这与 `skill:study_intake` 最初的「**开场必调**」设计**直接冲突**。冲突的后果不是模型二选一，而是
**它把纠结过程吐进了输出** —— 表现为可见的英文/中文推理痕迹（「The platform guideline says... But the study_intake skill explicitly overrides... There's tension.」）。

**修正**：把触发条件从「开场/打招呼」收窄为「**学习者表达明确学习需求**」：

- 纯问候（「你好」「在吗」）→ 正常文本回应，保持私教口吻，**不弹表单**
- 明确需求（「我想学 X」「帮我学 X」）→ 调用本技能，弹摸底表单

**两条 Prompt 教训**（已固化进 `skills/intake.py`）：

1. **不要在 Prompt 里写「反例」并原样引用要禁止的句子**。曾把
   「I've called study_intake. I should output the form via output_block.」作为反例写入，
   结果泄漏率从 1/2 升到 3/3 —— 负例反而强化了该行为。
2. **触发条件要写到 description 里，而不是只写 prompt**。description 决定「调不调」，
   prompt 只决定「调了之后怎么做」。

## 6. 交互流程（端到端）

```
[开场]
  无计划 → skill:study_intake → 开场语 + input.form 摸底
             （收集: 目标/基础/每日时长/截止日/知识来源）
         → skill:study_plan → 知识树
         → table 展示计划 + mermaid 学习路径 + input.confirm 确认
         → tool:learning_state.create_plan
  有计划 → load_plan → 「上次学到 kp-003,继续?」

[循环: 当前知识点 kp]
  1) teach  → skill:teach(angle=首个未试角度)
             抽象概念 → tool:image_gen 出图 → image 块
             讲解正文 → card / markdown / code
             出处 → kb_search / pdf_parse 的 source_span
  2) 记录   → tool:learning_state.record_teach
  3) 出题   → skill:quiz_generate
             客观题 → input.radio / input.checkbox
             主观题 → input.textarea
  4) 判分   → 主观题先 skill:quiz_grade 拿 rubric_score
             → tool:learning_state.submit_answers(全部 items)
  5) 分支
     action=advance → collapsible 展示解析 → tool:learning_state.advance
                      → 有下一个点则回到 [循环]，否则 → [收尾]
     action=reteach → 展示错因(collapsible) → 用 suggested_angle 重讲 → 回到 1)
     action=split   → 说明「换个更小颗粒度切入」→ skill:study_plan 生成子点
                      → tool:learning_state.split_kp → 回到 1)

[收尾]
  all_done → tool:learning_state.get_summary → skill:study_report
           → markdown 报告 + table 掌握度表 + 错题本
           → 可选: html_render 导出 PDF
```

**进度表**（每次推进后展示）：

```
| # | 知识点 | 难度 | 状态 | 掌握度 | 重讲次数 |
|---|--------|------|------|--------|----------|
| 1 | 向量与线性组合的直觉 | ★☆☆☆☆ | ✅ 已掌握 | 0.87 | 1 |
| 2 | 矩阵乘法的意义       | ★★☆☆☆ | 🔄 学习中 | 0.62 | 2 |
| 3 | 行列式与体积         | ★★☆☆☆ | ⏳ 未开始 | —   | 0 |
```

## 7. 平台能力复用对照

| 需求 | 复用能力 | 是否需声明依赖 |
|---|---|---|
| PDF 教材解析 | `tool:pdf_parse` | ✅ 声明 |
| 私有知识库检索 | `tool:kb_search` | ✅ 声明 |
| 教学示意图生成 | `tool:image_gen` | ✅ 声明 |
| 长材料摘要 | `skill:summarize` | ✅ 声明 |
| 知识树结构化 | `skill:structured_output` | ✅ 声明 |
| 联网补充/纠错 | `tool:web_search` | ❌ 默认可用 |
| 记住学习者画像 | `tool:memory` | ❌ 默认可用 |
| 每日学习任务落待办 | `tool:workbench_todo` | ❌ 默认可用 |
| 报告导出 PDF | `tool:html_render` | ❌ 默认可用（可选调用） |

**不自行实现**：PDF 解析、知识库检索、摘要、生图、联网搜索、结构化输出。

## 8. 工程结构

```
studyassistant/
├── plugin.yaml
├── pyproject.toml
├── conftest.py              # pytest 根配置：把工程根目录放进 sys.path
├── skills/
│   ├── study_plan.py        # skill:study_plan
│   ├── teach.py             # skill:study_teach（含 ANGLE_GUIDE 角度映射）
│   ├── quiz_generate.py     # skill:study_quiz_generate
│   ├── quiz_grade.py        # skill:study_quiz_grade
│   └── study_report.py      # skill:study_report
├── tools/
│   ├── __init__.py
│   ├── _mastery.py          # 掌握度纯函数（无 IO）
│   ├── _store.py            # JSON 持久化（无业务逻辑）
│   └── learning_state.py    # @tool 封装（_dispatch 纯逻辑 + 薄装饰器）
├── test/
│   ├── test_mastery.py      # 算法单测：判分/门槛/惩罚/轮换/收敛
│   ├── test_store.py        # 持久化单测：原子写/损坏降级/目录隔离
│   ├── test_learning_state.py  # 状态机单测：流转/推进/拆解/报告/重置
│   └── test_cases.yaml      # agentplatform test 用例
└── docs/
    ├── requirements/studyassistant.md
    ├── design/studyassistant.md
    ├── adr/000{1,2,3}-*.md
    └── tasks/studyassistant.md
```

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 模型不遵守「一次只讲一个点」 | 体验退化为一坨长文 | Prompt 强约束 + 讲解末尾固定抛检验问题；冒烟用例覆盖 |
| 模型跳过工具直接宣称「你已掌握」 | 核心闭环失效 | Prompt 红线声明 + `advance` 工具侧强校验（未达标直接拒绝） |
| 模型漏调 `record_teach`，导致建议角度不轮换 | 「换角度重讲」退化为重复同一讲法 | 工具自行记录 `angles_suggested`，`next_angle` 取「讲过 ∪ 建议过」的补集 |
| 主观题评分漂移 | 掌握度不稳定 | 评分要点分档（0/0.3/0.6/1.0）+ 客观题权重兜底 + 高权重门槛 |
| 状态文件损坏 | 学习记录丢失 | 原子写 + 读取降级 + `get_summary` 可随时导出快照 |
| 知识点拆解无限下钻 | 计划爆炸 | `max_split_depth` 限制，触顶后转为「标记为已知薄弱点，跳过并记录」 |
| 生图能力不可用 | 讲解降级 | `need_diagram` 为建议而非硬依赖，失败时用 ASCII/Markdown 图示兜底 |
