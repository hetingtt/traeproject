# 数据契约

三个 JSON 契约：试卷（quiz）、作答（submission）、批改报告（report）。
可直接复制 `assets/quiz-template.json` 与 `assets/submission-template.json` 起步。

---

## 1. 试卷 quiz.json

```jsonc
{
  "quiz_id": "字符串，必填，唯一标识",
  "title": "字符串，必填，试卷标题",
  "topic": "字符串，选填，主题/知识点范围",
  "difficulty": "easy | medium | hard，选填，整卷目标难度",
  "language": "选填，如 zh-CN",
  "grade_bands": [            // 选填，覆盖默认等级档位
    [90, "A", "优秀"],
    [0,  "F", "不及格"]
  ],
  "questions": [ /* 见下，必填，非空数组 */ ]
}
```

### 题目对象

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 题目唯一标识，整卷不重复 |
| `type` | string | 是 | `single_choice` / `multiple_choice` / `true_false` / `fill_blank` / `short_answer` |
| `stem` | string | 是 | 题干 |
| `options` | array | 选择/多选题必填 | `[{"key":"A","text":"..."}]` |
| `answer` | 视题型 | 是 | 见下方「answer 格式」 |
| `points` | number | 否 | 分值，默认 `1`，必须为正数 |
| `difficulty` | string | 否 | 默认 `medium` |
| `cognitive_level` | string | 否 | 默认 `understand`，取值见 `quality-standards.md` |
| `knowledge_point` | string | 否 | 用于知识点维度统计，建议必填 |
| `explanation` | string | 否 | 答案解析，批改反馈会引用 |
| `distractor_rationale` | object | 否 | 单选/多选干扰项辨析 `{"A":"..."}` |
| `misconception` | string | 否 | 判断题典型误区说明 |
| `grading` | object | 否 | 见下 |

### `answer` 格式（按题型）

| type | answer 示例 |
|------|-------------|
| `single_choice` | `"B"` |
| `multiple_choice` | `["A", "C", "D"]` |
| `true_false` | `true` 或 `false`（布尔值） |
| `fill_blank` | `[["HTTP", "超文本传输协议"], ["80"]]`（每个空一组可接受答案） |
| `short_answer` | `"参考答案全文"` |

### `grading` 格式

```jsonc
{
  "partial_credit": false,          // 仅 multiple_choice 使用
  "rubric": [                       // 仅 short_answer 使用
    { "point": "要点描述", "keywords": ["关键词1", "关键词2"], "score": 4 }
  ]
}
```

---

## 2. 作答 submission.json

```jsonc
{
  "quiz_id": "必须与试卷 quiz_id 一致",
  "learner": "选填，学习者标识",
  "answers": {
    "q1": "B",                       // 单选：选项键或选项原文
    "q2": ["A", "B", "D"],           // 多选：数组，顺序无关
    "q3": "对",                      // 判断：true/false/对/错/√/×...
    "q4": ["80", "443"],             // 填空：按空顺序，缺省的空视为未作答
    "q5": "简答题的自由文本作答"
  }
}
```

规则：
- `answers` 的键必须对应试卷中的题目 `id`；多余的键会被忽略。
- 缺失的键视为未作答，记 0 分并计入 `summary.unanswered`。
- 填空题也可以直接给字符串（此时视为只有一空）。

---

## 3. 批改报告 report.json

```jsonc
{
  "quiz_id": "demo-001",
  "title": "计算机网络基础 随堂测",
  "topic": "计算机网络基础",
  "difficulty": "medium",
  "total_score": 24.0,          // 实际得分
  "max_score": 29.0,            // 满分
  "percentage": 82.76,          // 得分率（百分制，保留两位）
  "grade": "B",                 // 等级
  "grade_label": "良好",
  "passed": true,               // percentage >= 60
  "summary": {
    "question_count": 5,
    "correct": 4,
    "wrong": 1,
    "unanswered": 0,
    "accuracy": 80.0,
    "needs_manual_review": 1,   // 需模型语义复核的题数
    "by_type": {
      "single_choice": { "count": 1, "correct": 1, "score": 5.0, "max_score": 5.0 }
    },
    "by_knowledge_point": {
      "TCP 连接管理": { "count": 2, "correct": 1, "score": 5.0, "max_score": 15.0 }
    }
  },
  "results": [
    {
      "id": "q1",
      "type": "single_choice",
      "stem": "TCP 建立连接时使用的握手次数是？",
      "options": [ { "key": "A", "text": "两次" } ],
      "difficulty": "easy",
      "cognitive_level": "remember",
      "knowledge_point": "TCP 连接管理",
      "user_answer": "B",
      "correct_answer": "B",
      "reference_answer": null,
      "score": 5.0,
      "max_score": 5.0,
      "is_correct": true,
      "needs_manual_review": false,
      "feedback": "正确，选 B。",
      "explanation": "TCP 通过三次握手……",
      "matched_points": null,        // 仅简答题有值
      "missing_points": null,        // 仅简答题有值
      "semantic_similarity": null    // 仅简答题有值
    }
  ]
}
```

---

## 4. 命令与退出码

```bash
python scripts/grade_quiz.py --quiz quiz.json --answers answers.json
python scripts/grade_quiz.py --quiz quiz.json --answers answers.json --output report.json
python scripts/grade_quiz.py --quiz quiz.json --answers answers.json --pretty
```

| 退出码 | 含义 |
|--------|------|
| 0 | 批改成功 |
| 1 | 输入数据不合法（结构错误 / 缺字段 / id 重复 / 题型不支持） |
| 2 | 文件读取或命令行参数错误 |

`--pretty` 输出人类可读文本报告；默认输出 JSON 到标准输出。
