"""技能：主观题判分与错因诊断。

职责边界（关键）：本技能**只给 0~1 的分**，不做「是否掌握」的判定。
聚合与达标判定归 ``tools/_mastery.py`` —— 判定权不在模型手里，防止讨好倾向
让闭环提前退出（见 docs/adr/0002）。
"""
from typing import Any

from agentplatform.sdk import Context, Skill, skill


@skill(
    id="skill:study_quiz_grade",
    version="0.1.0",
    description="依据评分要点给主观题打分（0~1）并诊断错因；不做达标判定",
    schema={
        "type": "object",
        "properties": {
            "kp_title": {"type": "string", "description": "知识点名称"},
            "question": {"type": "string", "description": "题干"},
            "rubric": {"type": "string", "description": "评分要点（分档描述）"},
            "user_answer": {"type": "string", "description": "学习者的作答原文"},
        },
        "required": ["question", "user_answer"],
    },
    prompt="""你是一位阅卷老师。请依据评分要点给学习者的作答打分。

## 知识点
{{kp_title}}

## 题目
{{question}}

## 评分要点
{{rubric}}

## 学习者作答
{{user_answer}}

## 评分要求
1. 只给 0~1 之间的一个分数，落在 0 / 0.3 / 0.6 / 1.0 这些档位上。
2. **严格按评分要点打分**。不要因为作答篇幅长、态度好、语气自信而给高分。
3. 完全没答到要点给 0；部分正确按档位给分；完全正确给 1.0。
4. `diagnosis` 必须指出**具体错在哪个子概念**，而不是笼统说「理解不深」「基础不牢」。
5. `missing_points` 列出作答中缺失的关键要点。
6. 不要说「你已经掌握了」这类话 —— 是否达标由验证环节判定，不由你决定。

## 输出纪律（严格遵守）
正文只写面向学习者的内容。以下内容**一律不得出现在正文**：
- 你的思考过程、判断理由、下一步打算
- 工具调用的说明或叙述
- 工具调用失败后的报错原文与排查步骤

工具调用失败或需要重试时，**静默处理**：直接换一种方式完成，不要叙述失败与恢复过程。
确实无法完成时，只用一句面向学习者的话说明结果，不得出现任何英文独白或过程性描述。

## 输出格式
只输出一个 JSON 对象，不要解释文字、不要 Markdown 代码围栏：

{"rubric_score": 0.6, "diagnosis": "…", "missing_points": ["…"], "tags": ["子概念"]}""",
)
class QuizGradeSkill(Skill):
    def execute(self, ctx: Context, args: dict[str, Any]) -> str:
        args.setdefault("kp_title", "（未指定）")
        args.setdefault("rubric", "（未提供评分要点，请按该题的一般正确性标准评分）")
        return self.render(args)
