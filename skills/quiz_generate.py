"""技能：针对知识点出验证题（含标准答案与评分要点）。

出题时必须同时给出 ``answer_key``（客观题）或 ``rubric``（主观题），
这样 ``tool:learning_state`` 才能对客观题做**确定性判分**，
模型也才有依据给主观题打分。
"""
from typing import Any

from agentplatform.sdk import Context, Skill, skill

DEFAULT_TYPE_MIX = "2 道 single + 1 道 multi + 1 道 short + 1 道 apply"


@skill(
    id="skill:study_quiz_generate",
    version="0.1.0",
    description="针对知识点设计验证题，强制产出标准答案(answer_key)与评分要点(rubric)",
    schema={
        "type": "object",
        "properties": {
            "kp_title": {"type": "string", "description": "知识点名称"},
            "acceptance": {"type": "string", "description": "该知识点的验收标准"},
            "difficulty": {"type": "integer", "description": "难度 1~5"},
            "count": {"type": "integer", "description": "题目数量，默认 5"},
            "type_mix": {"type": "string", "description": "题型配比说明，可选"},
            "weak_points": {
                "type": "string",
                "description": "学习者已暴露的薄弱子概念，应针对性出题，可选",
            },
            "source_material": {
                "type": "string",
                "description": "教材/知识库检索结果；提供时题目必须以之为准，可选",
            },
        },
        "required": ["kp_title"],
    },
    prompt="""你是一位出题老师。请针对给定知识点设计验证题，目的是判断学习者**是否真的掌握**。

## 知识点
- 名称：{{kp_title}}
- 难度：{{difficulty}}/5
- 怎样才算学会：{{acceptance}}

## 需要重点检验的薄弱子概念
{{weak_points}}

## 参考资料
{{source_material}}

## 出题要求
1. 共出 {{count}} 题，题型配比参考：{{type_mix}}
2. 题型取值：single（单选）、multi（多选）、short（简答）、apply（应用/推导）
3. **至少 1 道 short 或 apply** —— 只有这类题能证明「真会」。全是选择题的卷子不合格。
4. 每题必须直接检验验收标准中的某一条，并在 tags 中标注对应的子概念。
5. 干扰项要针对**典型误解**设计，不要凑数；错误选项必须看起来合理。
6. 不要出「以下说法哪个正确」这类脱离情境的题。
7. 题目之间不得互相提示答案。

## 每题必须给出
- single / multi：`answer_key`。single 为单个大写字母（如 "B"）；multi 为排序后的大写字母串（如 "ACD"）。
- short / apply：`rubric`，按 1.0 / 0.6 / 0.3 / 0 四档描述「什么样的作答对应什么分数」。

## 呈现给学习者（重要）
出完题后，**必须用交互控件把题目呈现出来**，让学习者点选作答，而不是让 ta 打字回复。对应关系：

| 题型 | 控件 |
|---|---|
| single | `input.radio` |
| multi | `input.checkbox` |
| short / apply | `input.textarea` |

每题一个控件块，用 `output_block` 输出；题干放在控件前的 markdown 块里（或控件的 label）。
单选/多选的 `options` 用 `{"label": "A. ……", "value": "A"}` 形式，`value` 必须是单个大写字母，
以便判分时与 `answer_key` 确定性比对。

⚠️ **不要在正文里泄露 `answer_key` 或 `rubric`** —— 那是判分依据，学习者不该提前看到。

## 输出纪律（严格遵守）
正文只写面向学习者的内容。以下内容**一律不得出现在正文**：
- 你的思考过程、判断理由、下一步打算
- 工具调用的说明或叙述
- 工具调用失败后的报错原文与排查步骤

工具调用失败或需要重试时，**静默处理**：直接换一种方式完成，不要叙述失败与恢复过程。
确实无法完成时，只用一句面向学习者的话说明结果，不得出现任何英文独白或过程性描述。

## 输出格式
只输出一个 JSON 对象，不要解释文字、不要 Markdown 代码围栏：

{
  "questions": [
    {
      "qid": "q1",
      "type": "single",
      "stem": "题干",
      "options": ["A. …", "B. …", "C. …", "D. …"],
      "answer_key": "B",
      "tags": ["子概念"]
    },
    {
      "qid": "q2",
      "type": "short",
      "stem": "题干",
      "rubric": "1.0=…；0.6=…；0.3=…；0=…",
      "tags": ["子概念"]
    }
  ]
}""",
)
class QuizGenerateSkill(Skill):
    def execute(self, ctx: Context, args: dict[str, Any]) -> str:
        args.setdefault("difficulty", 3)
        args.setdefault("count", 5)
        args.setdefault("type_mix", DEFAULT_TYPE_MIX)
        args.setdefault("weak_points", "（暂无，按验收标准全面覆盖）")
        args.setdefault("acceptance", "（未提供，请按该知识点的常规掌握标准出题）")
        args.setdefault("source_material", "（未提供，请基于你的领域知识出题）")
        return self.render(args)
