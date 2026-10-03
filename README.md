# studyassistant · AI 私教学习助手

> 把「讲一遍」变成「直到你学会」

一个 [AgentPlatform](https://github.com/fangxiao) 智能体插件。AI 扮演一对一私教，
为学习目标制定计划、逐点分层讲解、出题验证掌握程度；**没学会就换一种讲法重讲，
多次无效就拆成更小的知识点**，直到学习者真正掌握为止。

---

## 核心差异

市面上多数「AI 学习助手」止步于第 1 步（生成计划/大纲）或第 2 步（出几道题），
**没有把「没学会 → 换一种讲法 → 再验证」这个循环真正闭环**。

> **AI 不通过「讲完了」结束，只通过「你答对了」结束。**

```
目标 → 知识树计划 → ┌─ 讲解 → 出题验证 → 判分诊断 ─┐
                    │      未达标 → 换角度重讲 ────┤
                    └────── 达标 → 解锁下一知识点 ──┘
                              ↓
                        全部达标 → 学习报告 + 错题本
```

---

## 三个设计要点

### 1. 「学会」的判定权不在模型手里

模型只做两件事：出题（带标准答案与评分要点）、给主观题打 0~1 的分。
**聚合与达标判定是纯 Python 函数**：

```
mastery = 加权得分率 × 尝试次数惩罚
达标 ⟺ mastery ≥ 0.8  且  简答/应用题平均得分率 ≥ 0.6
```

- 题型权重：`single=1.0 / multi=1.4 / short=2.0 / apply=2.5`
- 尝试惩罚：`max(0.7, 1 - 0.1×(round-1))`
- **高权重题型门槛**：防止靠答对一堆选择题蒙混过关

如果让模型说「你已经掌握了」，它会顺着用户说话，闭环会提前退出 —— 产品承诺就废了。
分数必须可复现、可单测、可解释（`0.70 = 0.78 × 0.9`）。

### 2. 「没学会继续讲」= 换角度，不是重复

按固定序列取**尚未用过**的讲法，而不是把同样的话再说一遍：

```
直觉类比 → 图示 → 实例推演 → 对比辨析 → 严谨定义 → 追问引导
```

听不懂通常不是「听漏了」，而是讲法与学习者的认知结构不匹配。

### 3. 保证收敛，不死循环

「直到学会为止」字面直译会卡死会话。两级兜底：

| 触发条件 | 动作 |
|---|---|
| 重讲 3 轮仍不达标 | 判定为**颗粒度太粗**，拆成 2~4 个更小的知识点 |
| 拆解到 2 层仍不行 | 标记为薄弱点、写进错题本、**允许跳过**继续推进 |

在一个点上耗死，不如带着标记前进 —— 学习是长跑。

---

## 复用的平台公共能力

本插件**不重复实现**平台已有的底层能力：

| 用途 | 复用能力 |
|---|---|
| PDF 教材解析 | `tool:pdf_parse` |
| 私有知识库检索 | `tool:kb_search` |
| 教学示意图生成 | `tool:image_gen` |
| 长材料摘要 | `skill:summarize` |
| 知识树结构化 | `skill:structured_output` |
| 联网补充 / 纠错 | `tool:web_search`（平台默认可用） |
| 学习者画像记忆 | `tool:memory`（平台默认可用） |

## 富交互

讲解与验证过程通过平台 ContentBlock 呈现：`input.form`（摸底）、`input.radio` /
`input.checkbox` / `input.textarea`（答题）、`table`（进度表）、`collapsible`（答案解析）、
`mermaid`（关系图）、`image`（示意图）。

---

## 快速开始

```bash
# 安装依赖
uv venv .venv
uv pip install --python .venv/bin/python agentplatform pyyaml pytest pytest-asyncio

# 单元测试
.venv/bin/python -m pytest test/ -q

# 静态校验
agentplatform validate .

# 自动化用例
agentplatform test .

# 本地对话调试
agentplatform dev .

# 部署到平台
agentplatform deploy .
```

## 目录结构

```
.
├── plugin.yaml              # 插件清单：模型、依赖、自有 skills/tools
├── pyproject.toml
├── conftest.py              # pytest 根配置
├── skills/                  # Prompt 思考链（非确定性）
│   ├── intake.py            #   摸底建档
│   ├── study_plan.py        #   知识树拆解
│   ├── teach.py             #   多角度分层讲解（含 ANGLE_GUIDE）
│   ├── quiz_generate.py     #   出题（含答案与评分要点）
│   ├── quiz_grade.py        #   主观题判分与错因诊断
│   └── study_report.py      #   阶段学习报告
├── tools/                   # 确定性 Python（可单测）
│   ├── _mastery.py          #   掌握度算法（纯函数，无 IO）
│   ├── _store.py            #   JSON 持久化（原子写 + 损坏降级）
│   └── learning_state.py    #   状态机工具（11 个 action）
├── test/
│   ├── test_mastery.py
│   ├── test_store.py
│   ├── test_learning_state.py
│   └── test_cases.yaml
└── docs/
    ├── requirements/        # 需求：13 条用户故事 · 14 条验收标准
    ├── design/              # 技术设计
    ├── adr/                 # 架构决策记录
    └── tasks/               # 任务拆解
```

## 状态存储

学习进度落盘于 `~/.studyassistant/`（可用环境变量 `STUDYASSISTANT_DATA_DIR` 覆盖）：

```
~/.studyassistant/
├── active.json              # 当前活跃计划指针
└── plans/<plan_id>.json     # 计划全量状态（进度/掌握度/错题本/事件流）
```

写入采用**临时文件 + 原子替换**；读取失败降级返回而非抛异常。
支持跨会话「继续学上次的」。

---

## 设计文档

- [需求文档](docs/requirements/studyassistant.md) — 用户故事与验收标准
- [技术设计](docs/design/studyassistant.md) — 架构、状态模型、算法、技能规格
- 架构决策记录
  - [ADR-0001 进度持久化](docs/adr/0001-progress-persistence.md)
  - [ADR-0002 掌握度确定性判定](docs/adr/0002-deterministic-mastery-scoring.md)
  - [ADR-0003 换角度重讲与降级拆解](docs/adr/0003-reteach-and-split-strategy.md)
- [任务拆解](docs/tasks/studyassistant.md)

## 测试

```bash
.venv/bin/python -m pytest test/ -q
```

覆盖：判分确定性、高权重门槛、尝试惩罚、角度轮换、收敛路径、状态机全流程、
持久化原子性与损坏降级。

## License

MIT
