"""技能：把状态工具导出的学习数据渲染成阶段性学习报告。"""
from typing import Any

from agentplatform.sdk import Context, Skill, skill


@skill(
    id="skill:study_report",
    version="0.1.0",
    description="依据 tool:learning_state 导出的学习数据撰写阶段性学习报告（Markdown）",
    schema={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "tool:learning_state action=get_summary 返回的 JSON 原文",
            },
            "learner_note": {
                "type": "string",
                "description": "学习者补充说明（如本周状态、时间变化），可选",
            },
        },
        "required": ["summary"],
    },
    prompt="""你是一位私教老师，请根据学习数据为学习者写一份阶段性学习报告。

## 学习数据（JSON）
{{summary}}

## 学习者补充说明
{{learner_note}}

## 撰写要求
1. 用 Markdown，结构依次为：概览 → 进度明细 → 掌握度分析 → 薄弱环节 → 下一步建议。
2. **进度明细**用表格，列为：序号 | 知识点 | 难度 | 状态 | 掌握度 | 重讲次数。
   - 状态标记：✅ 已掌握 / 🔄 学习中 / ⏳ 未开始 / ⏭️ 已跳过 / 📦 已拆解
   - 掌握度保留两位小数；未开始的显示 `—`
   - 难度用 ★ 表示（1~5 颗星）
3. **掌握度分析**：说明哪些点一次通过、哪些点反复多轮，以及反复的原因线索。
4. **薄弱环节**：基于 weak_top 与 wrong_book，指出**具体哪个子概念**反复出错，并引用实际错题。
5. **下一步建议**：具体可执行，包含建议攻克的下一个知识点、建议用时、以及针对薄弱点的做法。
6. 语气客观平实。数据不好看就如实说，不要浮夸鼓励，也不要空洞地说「继续加油」。
7. 只依据给定的数据，不要编造未出现的知识点或成绩。

## 输出纪律（严格遵守）
正文只写面向学习者的内容。以下内容**一律不得出现在正文**：
- 你的思考过程、判断理由、下一步打算
- 工具调用的说明或叙述
- 工具调用失败后的报错原文与排查步骤

工具调用失败或需要重试时，**静默处理**：直接换一种方式完成，不要叙述失败与恢复过程。
确实无法完成时，只用一句面向学习者的话说明结果，不得出现任何英文独白或过程性描述。

直接输出报告正文。""",
)
class StudyReportSkill(Skill):
    def execute(self, ctx: Context, args: dict[str, Any]) -> str:
        args.setdefault("learner_note", "（无）")
        return self.render(args)
