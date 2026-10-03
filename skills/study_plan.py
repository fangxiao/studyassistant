"""技能：把学习目标拆解成有序、可验证、颗粒度合适的知识树。"""
from typing import Any

from agentplatform.sdk import Context, Skill, skill


@skill(
    id="skill:study_plan",
    version="0.1.0",
    description="把学习目标拆解成有序、可验证、颗粒度合适的知识树，产出可直接交给 tool:learning_state 建档的 JSON",
    schema={
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "学习目标，例如「两个月内掌握线性代数核心概念」",
            },
            "current_level": {
                "type": "string",
                "enum": ["beginner", "intermediate", "advanced"],
                "description": "学习者当前水平",
            },
            "daily_minutes": {
                "type": "integer",
                "description": "每天可投入的学习分钟数",
            },
            "deadline": {"type": "string", "description": "目标截止日期，可选"},
            "domain": {"type": "string", "description": "所属领域/学科，可选"},
            "source_material": {
                "type": "string",
                "description": "教材、讲义或考纲节选；提供时知识点必须与之一致，可选",
            },
            "existing_progress": {
                "type": "string",
                "description": "学习者已掌握的内容，用于跳过已知部分，可选",
            },
        },
        "required": ["goal"],
    },
    prompt="""你是一位擅长「把大目标拆成可执行小步」的私教老师。请为学习者制定学习计划。

## 学习者情况
- 学习目标：{{goal}}
- 当前水平：{{current_level}}
- 每天可投入：{{daily_minutes}} 分钟
- 目标截止：{{deadline}}
- 所属领域：{{domain}}
- 已有基础：{{existing_progress}}

## 参考资料
{{source_material}}

## 拆解要求
1. **颗粒度**：单个知识点必须能在 {{daily_minutes}} 分钟内完成一轮「讲解 + 验证」。宁可拆细，不要贪大。
2. **可验证**：每个知识点给出 1~3 条 acceptance（验收标准），写成「学习者能做出什么具体行为」，例如「能用自己的话解释…」「能判断给定的…属于…」「能推导出…」。禁止写「理解…」「熟悉…」「了解…」这类无法验证的描述。
3. **顺序**：严格满足前置依赖，先直觉后形式、先具体后抽象。order 从 1 递增。
4. **难度**：difficulty 取 1~5，整体渐进上升，不要一上来就是 5。
5. **数量**：总数控制在 6~15 个。目标很大时先覆盖主干骨架，细节留待后续拆解。
6. **估时**：est_minutes 为该知识点的预估学习分钟数。
7. **对齐资料**：若提供了参考资料，知识点必须与资料内容一致，不要引入资料之外的体系。

## 输出纪律（严格遵守）
正文只写面向学习者的内容。以下内容**一律不得出现在正文**：
- 你的思考过程、判断理由、下一步打算
- 工具调用的说明或叙述
- 工具调用失败后的报错原文与排查步骤

工具调用失败或需要重试时，**静默处理**：直接换一种方式完成，不要叙述失败与恢复过程。
确实无法完成时，只用一句面向学习者的话说明结果，不得出现任何英文独白或过程性描述。

## 输出格式
只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码围栏：

{
  "goal": {"title": "一句话概括的学习目标"},
  "knowledge_points": [
    {
      "title": "知识点名称",
      "acceptance": ["能…", "能…"],
      "difficulty": 2,
      "est_minutes": 25
    }
  ]
}""",
)
class StudyPlanSkill(Skill):
    def execute(self, ctx: Context, args: dict[str, Any]) -> str:
        args.setdefault("current_level", "beginner")
        args.setdefault("daily_minutes", 30)
        args.setdefault("deadline", "未指定")
        args.setdefault("domain", "未指定")
        args.setdefault("existing_progress", "（未提供）")
        args.setdefault("source_material", "（未提供，请基于你的领域知识拆解）")
        return self.render(args)
