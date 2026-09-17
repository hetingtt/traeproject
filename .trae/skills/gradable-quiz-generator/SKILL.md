---
name: gradable-quiz-generator
description: Generate auto-gradable quizzes on a topic and grade submitted answers with detailed feedback. Use when the user asks to create practice questions, exam papers, or tests, or to grade and explain answers. Do not use for slide authoring.
---

# Gradable Quiz Generator

生成**可自动批改**的试卷，并对学习者作答给出评分与错误分析。所有题目落成 JSON 试卷，
由 `scripts/grade_quiz.py` 做确定性判定，保证同一份作答每次得到同样的分数。

技能目录下的文件（相对本技能根目录解析）：

- `scripts/grade_quiz.py` —— 批改引擎与命题自检
- `scripts/test_grade_quiz.py` —— 单元测试
- `references/question-types.md` —— 五种题型的命题规则与判定语义
- `references/quality-standards.md` —— 认知层级、难度分级、编写红线、反馈规范
- `references/io-schema.md` —— 试卷 / 作答 / 报告的完整字段契约
- `assets/quiz-template.json`、`assets/submission-template.json` —— 可直接复制的模板

---

## 工作流

### 第 1 步：确认需求参数

从用户请求中提取以下参数，未提及的用默认值，**不要逐项追问**：

| 参数 | 默认值 |
|------|--------|
| 主题 / 知识点范围 | 必填，缺失才追问 |
| 题型 | 全四种客观题 + 简答题（视主题合理性裁剪） |
| 题量 | 10 题 |
| 难度 | `medium`（分布 3:5:2） |
| 单题分值 | 客观题 5 分，简答题 10 分 |
| 语言 | 与用户一致 |
| 学习者水平 | 默认「有基础的学习者」 |

若用户只要题目不要批改，仍要生成完整答案键——没有答案键的题目不算合格交付。

### 第 2 步：设计命题蓝图

在写题前先定分布，避免题型和认知层级失衡：

1. 按题型分配题量（如 10 题 → 单选 4、多选 2、判断 2、填空 1、简答 1）。
2. 按 `references/quality-standards.md` 第 1 节分配 `cognitive_level`，按第 2 节分配 `difficulty`。
3. 列出要覆盖的知识点清单，每个知识点至少 1 题，且 `knowledge_point` 用词在全卷内保持一致。

### 第 3 步：编写试卷 JSON

先读 `references/io-schema.md` 确认字段，再按题型规则出题：

- 单选题、多选题、判断题、填空题、简答题的命题要求与判定语义见 `references/question-types.md`。
- 编写红线（单一考点、答案唯一、无答案泄露、选项同质等）见 `references/quality-standards.md` 第 3 节。
- 结构可从 `assets/quiz-template.json` 复制后改写。

每道题必须带齐：`knowledge_point`、`difficulty`、`cognitive_level`、`explanation`。
单选/多选补 `distractor_rationale`，判断题补 `misconception`，简答题补 `grading.rubric`。

### 第 4 步：命题自检（不可跳过）

用标准答案回灌引擎，验证答案键、评分要点与结构：

```bash
python <skill-dir>/scripts/grade_quiz.py --quiz <quiz.json> --self-check --pretty
```

- 退出码 `0` 表示通过；`1` 表示存在 `error`，**必须修复后重跑**。
- `error` 项：标准答案不在选项中、`rubric` 分值之和不等于 `points`、客观题自评未得满分、选项 key 重复。
- `warning` 项：缺 `knowledge_point` / `explanation`、评分要点关键词未出现在参考答案中、多选题正确项少于 2 个。warning 应尽量清零。

### 第 5 步：批改并输出反馈

```bash
python <skill-dir>/scripts/grade_quiz.py --quiz <quiz.json> --answers <answers.json> --output report.json
```

作答文件格式见 `references/io-schema.md` 第 2 节；可从 `assets/submission-template.json` 复制。

拿到报告后，**必须处理 `needs_manual_review = true` 的题目**（只可能是简答题）：脚本的关键词初筛会
漏掉同义表述，请逐题阅读学习者原文与参考答案，做语义判定，然后在最终输出中覆盖该题得分。
复核时以语义为准，不要被 `semantic_similarity` 数值绑架。

---

## 反馈输出规范

对学习者展示时，按 `references/quality-standards.md` 第 4 节给齐四要素：判定结论、标准答案、
错误分析、知识点讲解。要求：

- 先给整卷结论（总分、得分率、等级、是否通过），再逐题展开。
- 错误分析要指向具体误区，引用 `distractor_rationale` / `misconception` / `missing_points`，
  禁止只写「回答错误」。
- 末尾给出**薄弱知识点排序**（按 `summary.by_knowledge_point` 得分率升序取前 3）与复习建议。
- 被复核修正过的题目，注明「已人工复核」。

需要机器可读结果时直接交付 `report.json`，不要另造格式。

---

## 边界

- 本技能负责出题与批改，不负责生成课件、讲稿或知识点综述。
- 客观题判定一律以引擎结果为准，不要凭印象改分；只有简答题允许语义复核。
- 用户要求「只出题、不用批改」时，仍交付带完整答案键的试卷 JSON，并说明可直接用引擎批改。
- 修改引擎或新增题型后，运行 `python -m unittest discover -s <skill-dir>/scripts` 确认测试全绿。
