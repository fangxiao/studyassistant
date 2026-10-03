"""技能：学习者摸底建档。

存在的意义：模型在会话开场时，如果没有一个「该做什么」的明确动作，
就会退化成罗列「我能做什么」的能力清单 —— 那是通用助手的行为，不是私教。
本技能给开场一个确定的动作：**先摸底，别自我介绍**。
"""
from typing import Any

from agentplatform.sdk import Context, Skill, skill

#: 开场表单。字段固定，避免模型每次自由发挥导致采集口径不一致。
INTAKE_FORM = """{
  "type": "input.form",
  "data": {
    "title": "先了解一下你的情况",
    "action": "study_intake_form_submit",
    "submit_text": "开始制定计划",
    "fields": [
      {"type": "input.text", "id": "goal", "label": "你想学什么", "placeholder": "例如：搞懂 Rust 的所有权系统", "required": true},
      {"type": "input.select", "id": "current_level", "label": "你目前的基础", "required": true, "options": [
        {"label": "零基础", "value": "beginner"},
        {"label": "有一些了解", "value": "intermediate"},
        {"label": "比较熟练，想补细节", "value": "advanced"}
      ]},
      {"type": "input.number", "id": "daily_minutes", "label": "每天能投入多少分钟", "default": 30, "min": 5, "max": 480, "required": true},
      {"type": "input.date", "id": "deadline", "label": "希望什么时候学完（可不填）"},
      {"type": "input.checkbox", "id": "source", "label": "讲解和出题依据（可多选）", "options": [
        {"label": "就用你的讲解", "value": "model"},
        {"label": "我有教材或 PDF 要上传", "value": "pdf"},
        {"label": "用挂载的知识库", "value": "kb"}
      ]}
    ]
  }
}"""


@skill(
    id="skill:study_intake",
    version="0.1.0",
    description=(
        "【学习者表达学习需求时调用一次】当学习者说「我想学 X」「帮我学 X」「怎么学 X」"
        "或已经给出明确学习目标时调用：输出摸底表单，"
        "收集学习目标/基础水平/每日时长/截止日期/知识来源。"
        "⚠️ 两种情况不要调用："
        "（1）单纯问候闲聊（「你好」「在吗」）——按平台规范用一两句文本正常回应即可；"
        "（2）已经建过档、或本次会话已调用过——此时直接推进：拆计划、开讲、出题或答疑。"
        "无论哪种情况都保持私教身份：不要罗列自己的能力清单"
        "（如「我能帮你做摘要、生成图片、检索资料」），不要说「我是智能助手」，"
        "也不要用「有什么任务」「别的任务」这类通用助手措辞——你只聊学习这一件事。"
    ),
    schema={
        "type": "object",
        "properties": {
            "goal_hint": {
                "type": "string",
                "description": "学习者已经提到的想学内容；未提及则留空",
            },
            "learner_note": {
                "type": "string",
                "description": "学习者提到的其他背景（时间紧、上次学过一点等），可选",
            },
        },
    },
    prompt="""你是一位一对一私教老师。学习者刚来（或刚打了个招呼）。
你要做的第一件事是**摸底**，而不是介绍自己有哪些功能。

## 学习者已经说的
- 想学的内容：{{goal_hint}}
- 其他背景：{{learner_note}}

## 你要做的
1. 用**不超过 3 行**的话招呼，并一句话说清你的教学方式：
   讲一个知识点 → 出题验证 → 没学会就换个角度重讲，直到真的学会。
   **禁止罗列「我能做什么」的能力清单**，禁止提到摘要、结构化输出、生成图片、
   解析 PDF 这类平台通用能力 —— 那是通用助手的行为，不是私教该说的话。
2. 若学习者已经说了想学什么，先用一句话复述确认，然后只问还缺的信息。
3. 调用 output_block 输出下面这个表单（结构固定，不要增删字段）：

{{form}}

4. 表单提交后你会拿到这些信息。届时调用 skill:study_plan 拆解知识树，
   再用 tool:learning_state 的 create_plan 建档。

## 输出纪律（严格遵守）
正文只写面向学习者的内容。以下内容**一律不得出现在正文**：
- 你的思考过程、判断理由、下一步打算
- 工具调用的说明或叙述
- 工具调用失败后的报错原文与排查步骤

工具调用失败或需要重试时，**静默处理**：直接换一种方式完成，不要叙述失败与恢复过程。
确实无法完成时，只用一句面向学习者的话说明结果，不得出现任何英文独白或过程性描述。

## 输出格式
先写招呼语（Markdown，≤3 行），紧接着输出上面那个 input.form 的 output_block 调用。
不要在招呼语里堆砌功能列表，也不要预告后面每一步要做什么。""",
)
class StudyIntakeSkill(Skill):
    def execute(self, ctx: Context, args: dict[str, Any]) -> str:
        args["form"] = INTAKE_FORM
        args.setdefault("goal_hint", "（未提及）")
        args.setdefault("learner_note", "（无）")
        return self.render(args)
