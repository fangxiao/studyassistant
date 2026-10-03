"""技能：多角度分层讲解单个知识点。

「没学会就换一种讲法」的落点：``angle`` 决定本次用什么方式讲。
角度序列与轮换逻辑由 ``tools/_mastery.py`` 的 ``TEACH_ANGLES`` / ``next_angle`` 决定，
本技能只负责把选定角度翻译成具体的讲解指令。
"""
from typing import Any

from agentplatform.sdk import Context, Skill, skill

#: 每个角度对应的讲解指令。键必须与 _mastery.TEACH_ANGLES 保持一致。
ANGLE_GUIDE: dict[str, str] = {
    "intuition": "用生活化的类比建立直觉。先让学习者「感觉对」，暂不追求严谨。类比要贴切，并明确指出这个类比在哪里会失效。",
    "visual": "用图形化方式讲。描述结构、层次、流向与变化过程，让学习者能在脑中画出画面。必须设置 need_diagram=true 并给出可直接用于生图的 diagram_prompt（若用 Mermaid 更合适，见下方「配图方式怎么选」）。",
    "example": "用一个完整的、从零推演的具体实例贯穿讲解，每一步都写清「为什么这一步可以这么做」，不要跳步。",
    "contrast": "通过对比辨析来讲：与最容易混淆的概念逐条对比，指出关键差异、各自适用场景，以及混淆后会得出什么错误结论。",
    "formal": "给出严谨定义、符号表示与推导过程。适合已有直觉、需要把理解精确化的学习者。",
    "socratic": "不要直接给结论。用一连串递进的问题引导学习者自己推出结论，每个问题后只给简短提示，把最后一步留给学习者。",
}

_ANGLE_LABELS = {
    "intuition": "直觉类比",
    "visual": "图示",
    "example": "实例推演",
    "contrast": "对比辨析",
    "formal": "严谨定义",
    "socratic": "追问引导",
}


@skill(
    id="skill:study_teach",
    version="0.1.0",
    description="按指定角度讲解单个知识点，一次只讲一个点，结尾抛出检验性问题",
    schema={
        "type": "object",
        "properties": {
            "kp_title": {"type": "string", "description": "知识点名称"},
            "acceptance": {
                "type": "string",
                "description": "该知识点的验收标准（怎样才算学会）",
            },
            "angle": {
                "type": "string",
                "enum": list(ANGLE_GUIDE.keys()),
                "description": "本次讲解角度；由 tool:learning_state 的 suggested_angle 给出",
            },
            "difficulty": {"type": "integer", "description": "难度 1~5"},
            "learner_level": {"type": "string", "description": "学习者水平"},
            "weak_points": {
                "type": "string",
                "description": "已发现的薄弱子概念，讲解时要重点澄清，可选",
            },
            "source_material": {
                "type": "string",
                "description": "教材/知识库检索结果；提供时讲解必须以之为准并标注出处，可选",
            },
        },
        "required": ["kp_title", "angle"],
    },
    prompt="""你是一位一对一私教老师。请讲解**一个**知识点。

## 知识点
- 名称：{{kp_title}}
- 难度：{{difficulty}}/5
- 怎样才算学会：{{acceptance}}

## 学习者
- 水平：{{learner_level}}
- 已发现的薄弱点：{{weak_points}}

## 参考资料
{{source_material}}

## 本次讲解角度：{{angle_label}}
{{angle_guide}}

## 硬性要求
1. **只讲这一个知识点**，不要顺带展开下一个知识点。
2. 篇幅 400~700 字。宁可少而透，不要多而泛。
3. 至少给 1 个具体例子，不要用「例如 A、B」这种占位式举例。
4. 若参考资料中给出了出处（文档名/位置），讲解时标注来源。
5. 若这个知识点适合用图表达，设置 need_diagram=true 并给出 diagram_prompt（主体、结构、标注、风格的详细描述）。
6. **结尾必须抛出一个检验性问题**，让学习者先自己想，不要直接给答案。
7. 禁止说「你已经掌握了」这类话 —— 是否掌握由验证环节判定，不由你决定。

## 配图方式怎么选（重要）
若 need_diagram=true，先判断该用哪种方式，**讲完正文后必须真的把图画出来并内嵌展示**：

**① 能用「节点 + 箭头」表达的 → Mermaid**
流程、时序、状态流转、层级结构、依赖关系等。
用 `output_block` 输出 mermaid 块：{"type": "mermaid", "data": {"code": "graph TD; A-->B;"}}
⚠️ Mermaid 语法必须合法。**画不出来的东西不要硬塞** —— 语法不合法会渲染成一片空白。

**② 不能的 → 生图（image_gen）**
几何图形、空间关系、数学示意、具象插图、需要真实画面感的图（例如矩阵行列的排布、函数曲线、物理示意）。
步骤：
1. 调用 `tool:image_gen`，prompt 用你写的 diagram_prompt；
2. 拿到返回的图片 URL 后，调用 `output_block` 输出图片块：
   {"type": "image", "data": {"url": "<返回的URL>", "alt": "<图的内容简述>", "caption": "<图注>"}}
3. **把图片直接内嵌展示给学习者**，不要只贴一个链接让 ta 自己点过去。

**判断口诀**：Mermaid 画「关系」，生图画「样子」。拿不准就用生图。
若生图不可用或失败，用文字 / ASCII 图示兜底，并说明这次没配图。

## 输出纪律（严格遵守）
正文只写面向学习者的内容。以下内容**一律不得出现在正文**：
- 你的思考过程、判断理由、下一步打算
- 工具调用的说明或叙述
- 工具调用失败后的报错原文与排查步骤

工具调用失败或需要重试时，**静默处理**：直接换一种方式完成，不要叙述失败与恢复过程。
确实无法完成时，只用一句面向学习者的话说明结果，不得出现任何英文独白或过程性描述。

## 输出格式
先输出讲解正文（Markdown），正文结束后另起一行，附一个 JSON 对象：

{"need_diagram": false, "diagram_prompt": "", "check_question": "检验性问题"}""",
)
class TeachSkill(Skill):
    def execute(self, ctx: Context, args: dict[str, Any]) -> str:
        angle = str(args.get("angle") or "intuition")
        if angle not in ANGLE_GUIDE:
            angle = "intuition"
        args["angle"] = angle
        args["angle_label"] = _ANGLE_LABELS[angle]
        args["angle_guide"] = ANGLE_GUIDE[angle]

        args.setdefault("difficulty", 3)
        args.setdefault("learner_level", "beginner")
        args.setdefault("weak_points", "（暂无）")
        args.setdefault("acceptance", "（未提供，请按该知识点的常规掌握标准讲解）")
        args.setdefault("source_material", "（未提供，请基于你的领域知识讲解）")
        return self.render(args)
