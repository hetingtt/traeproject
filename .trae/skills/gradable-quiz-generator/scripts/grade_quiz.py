#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可批改题目自动评分引擎。

职责
----
读取一份「试卷 JSON」（含标准答案与评分依据）和一份「作答 JSON」（学习者提交），
输出结构化批改报告：逐题得分、正误判定、错误分析、知识点、标准答案与解析。

设计原则
--------
1. 确定性：客观题（单选 / 多选 / 判断 / 填空）完全由本脚本判定，结果可复现。
2. 保守性：主观题（简答）只做关键词与相似度的初筛，凡不能确证满分的情况一律
   打上 ``needs_manual_review`` 标记，交由上层模型做语义复核，避免误判学习者。
3. 零依赖：仅使用 Python 标准库，便于在任何环境中直接运行。

用法
----
    python grade_quiz.py --quiz quiz.json --answers answers.json
    python grade_quiz.py --quiz quiz.json --answers answers.json --output report.json
    python grade_quiz.py --quiz quiz.json --answers answers.json --pretty

退出码
------
    0  批改成功
    1  输入数据不合法（结构错误 / 缺字段 / 引用不存在的题目）
    2  文件读取或命令行参数错误
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 常量：默认评分档位（百分制）
# ---------------------------------------------------------------------------

DEFAULT_GRADE_BANDS: List[Tuple[float, str, str]] = [
    (90.0, "A", "优秀"),
    (80.0, "B", "良好"),
    (70.0, "C", "中等"),
    (60.0, "D", "及格"),
    (0.0, "F", "不及格"),
]

# 判定主观题「语义完全一致」的相似度阈值
SHORT_ANSWER_SIMILARITY_HINT = 0.72

TRUE_TOKENS = {"true", "t", "1", "yes", "y", "对", "正确", "是", "√", "v"}
FALSE_TOKENS = {"false", "f", "0", "no", "n", "错", "错误", "否", "×", "x"}

# 归一化时需要剔除的标点与空白
_PUNCT_RE = re.compile(r"[\s\u3000]+")
_TRIM_CHARS = " \t\r\n\u3000.,;:!?。，；：！？、\"'“”‘’()（）[]【】<>《》-—_/\\|~`*#"


class QuizFormatError(ValueError):
    """输入数据不符合试卷 / 作答契约时抛出。"""


# ---------------------------------------------------------------------------
# 文本归一化工具
# ---------------------------------------------------------------------------


def normalize_text(value: Any) -> str:
    """把任意文本压成可比较的规范形式。

    处理内容：全角转半角、大小写统一、剔除标点与所有空白、统一 Unicode 形式。
    这样「HTTP 协议」与「http协议」会被视为同一答案。
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = _PUNCT_RE.sub("", text)
    return text.strip(_TRIM_CHARS)


def parse_bool(value: Any) -> Optional[bool]:
    """把各种常见的判断题作答形式解析为布尔值，无法解析时返回 None。"""
    if isinstance(value, bool):
        return value
    token = normalize_text(value)
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    return None


def similarity(a: Any, b: Any) -> float:
    """基于字符序列的相似度（0.0 ~ 1.0），用于主观题初筛。"""
    left, right = normalize_text(a), normalize_text(b)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


# ---------------------------------------------------------------------------
# 试卷与作答解析
# ---------------------------------------------------------------------------


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QuizFormatError(message)


def load_json(path: str) -> Dict[str, Any]:
    """读取 JSON 文件并确保顶层是对象。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise QuizFormatError(f"文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise QuizFormatError(f"JSON 解析失败（{path}）：{exc}") from exc
    _require(isinstance(data, dict), f"{path} 顶层必须是 JSON 对象")
    return data


def normalize_quiz(quiz: Dict[str, Any]) -> Dict[str, Any]:
    """校验并补全试卷结构，返回规范化后的题目列表。"""
    questions = quiz.get("questions")
    _require(isinstance(questions, list) and questions, "试卷缺少非空的 questions 数组")

    seen_ids = set()
    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(questions):
        _require(isinstance(raw, dict), f"第 {index + 1} 题不是对象")
        qid = str(raw.get("id") or f"q{index + 1}")
        _require(qid not in seen_ids, f"题目 id 重复：{qid}")
        seen_ids.add(qid)

        qtype = raw.get("type")
        _require(
            qtype in {"single_choice", "multiple_choice", "true_false", "fill_blank", "short_answer"},
            f"题目 {qid} 的 type 不合法：{qtype!r}",
        )
        _require(raw.get("stem"), f"题目 {qid} 缺少 stem（题干）")
        _require(raw.get("answer") is not None, f"题目 {qid} 缺少 answer（标准答案）")

        points = raw.get("points", 1)
        _require(
            isinstance(points, (int, float)) and points > 0,
            f"题目 {qid} 的 points 必须是正数",
        )

        item = dict(raw)
        item["id"] = qid
        item["points"] = float(points)
        item["difficulty"] = raw.get("difficulty", "medium")
        item["cognitive_level"] = raw.get("cognitive_level", "understand")
        item["knowledge_point"] = raw.get("knowledge_point", "")
        item["explanation"] = raw.get("explanation", "")
        item["options"] = raw.get("options") or []
        item["grading"] = raw.get("grading") or {}
        normalized.append(item)

    quiz = dict(quiz)
    quiz["questions"] = normalized
    quiz.setdefault("quiz_id", "quiz")
    quiz.setdefault("title", quiz["quiz_id"])
    return quiz


def normalize_submission(submission: Dict[str, Any]) -> Dict[str, Any]:
    """校验并提取作答映射。"""
    answers = submission.get("answers")
    _require(isinstance(answers, dict), "作答文件缺少 answers 对象")
    return answers


# ---------------------------------------------------------------------------
# 各题型判定逻辑
# ---------------------------------------------------------------------------


def _option_key(question: Dict[str, Any], value: Any) -> Optional[str]:
    """把作答值映射为选项键（支持直接写选项键或写选项原文）。"""
    token = normalize_text(value)
    if not token:
        return None
    for option in question["options"]:
        key = str(option.get("key", "")).strip()
        if token == normalize_text(key):
            return key.upper()
    for option in question["options"]:
        key = str(option.get("key", "")).strip()
        if token and token == normalize_text(option.get("text", "")):
            return key.upper()
    return None


def _fmt_options(question: Dict[str, Any]) -> str:
    parts = [f"{o.get('key')}. {o.get('text')}" for o in question["options"]]
    return "  ".join(parts) if parts else "（无选项）"


def grade_single_choice(question: Dict[str, Any], user_answer: Any) -> Dict[str, Any]:
    """单选题：选项键精确匹配即满分，否则零分。"""
    correct_key = _option_key(question, question["answer"]) or normalize_text(question["answer"]).upper()
    user_key = _option_key(question, user_answer)

    if user_key is None:
        if normalize_text(user_answer) == "":
            return _verdict(0.0, False, "未作答，未得分。", user_answer)
        return _verdict(
            0.0,
            False,
            f"作答 {user_answer!r} 不是有效选项。标准答案：{correct_key}。",
            user_answer,
        )
    if user_key == correct_key:
        return _verdict(question["points"], True, f"正确，选 {user_key}。", user_key)
    return _verdict(
        0.0,
        False,
        f"选了 {user_key}，标准答案是 {correct_key}。混淆项说明：{_distractor_hint(question, user_key)}",
        user_key,
    )


def _distractor_hint(question: Dict[str, Any], chosen_key: str) -> str:
    """从题目的 distractor_rationale 中取出学习者所选项的辨析说明。"""
    rationale = question.get("distractor_rationale") or {}
    if isinstance(rationale, dict) and rationale.get(chosen_key):
        return str(rationale[chosen_key])
    return "该项与题干条件不符，注意区分相近概念的适用边界。"


def grade_multiple_choice(question: Dict[str, Any], user_answer: Any) -> Dict[str, Any]:
    """多选题：默认全对才给分；开启 partial_credit 时按漏选比例给部分分。"""
    correct_raw = question["answer"]
    if not isinstance(correct_raw, (list, tuple, set)):
        correct_raw = [correct_raw]
    correct_keys = {k for k in (_option_key(question, v) for v in correct_raw) if k}
    correct_keys = {k.upper() for k in correct_keys} or {
        normalize_text(v).upper() for v in correct_raw
    }

    if user_answer is None or (isinstance(user_answer, str) and not user_answer.strip()) or user_answer == []:
        return _verdict(0.0, False, f"未作答，未得分。标准答案：{''.join(sorted(correct_keys))}。", [])

    if not isinstance(user_answer, (list, tuple, set)):
        user_answer = [user_answer]
    user_keys = {k for k in (_option_key(question, v) for v in user_answer) if k}
    if not user_keys:
        return _verdict(
            0.0,
            False,
            f"作答不是有效选项。标准答案：{''.join(sorted(correct_keys))}。",
            list(user_answer),
        )

    missed = sorted(correct_keys - user_keys)
    extra = sorted(user_keys - correct_keys)
    user_sorted = sorted(user_keys)
    correct_label = "".join(sorted(correct_keys))

    if not missed and not extra:
        return _verdict(question["points"], True, f"完全正确，选 {correct_label}。", user_sorted)

    partial_credit = bool(question["grading"].get("partial_credit", False))
    reasons = []
    if missed:
        reasons.append(f"漏选 {''.join(missed)}")
    if extra:
        reasons.append(f"多选/错选 {''.join(extra)}")
    reason_text = "，".join(reasons)

    if not partial_credit:
        return _verdict(
            0.0,
            False,
            f"{reason_text}。多选题需全部选对才得分，标准答案：{correct_label}。",
            user_sorted,
        )

    # 部分分策略：命中数 - 错选数，下限为 0，再按正确项占比折算
    hit = len(correct_keys & user_keys)
    score_ratio = max(0.0, hit - len(extra)) / len(correct_keys)
    score = round(question["points"] * score_ratio, 2)
    feedback = f"{reason_text}。按漏选比例给部分分，标准答案：{correct_label}。"
    return _verdict(score, False, feedback, user_sorted)


def grade_true_false(question: Dict[str, Any], user_answer: Any) -> Dict[str, Any]:
    """判断题：解析布尔值后精确比对。"""
    correct = parse_bool(question["answer"])
    if correct is None:
        raise QuizFormatError(f"题目 {question['id']} 的判断题标准答案无法解析为布尔值")
    user = parse_bool(user_answer)
    label = "正确" if correct else "错误"

    if user is None:
        if normalize_text(user_answer) == "":
            return _verdict(0.0, False, f"未作答，未得分。标准答案：{label}。", user_answer)
        return _verdict(
            0.0, False, f"作答 {user_answer!r} 无法识别为判断，标准答案：{label}。", user_answer
        )
    if user == correct:
        return _verdict(question["points"], True, f"正确，答案为「{label}」。", user)
    return _verdict(
        0.0,
        False,
        f"判断为「{'正确' if user else '错误'}」，标准答案是「{label}」。{question.get('misconception') or ''}".strip(),
        user,
    )


def _blank_accepted_sets(question: Dict[str, Any]) -> List[List[str]]:
    """把填空题答案统一成「每个空一组可接受答案」的结构。"""
    raw = question["answer"]
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    blanks: List[List[str]] = []
    for entry in raw:
        if isinstance(entry, (list, tuple, set)):
            blanks.append([str(v) for v in entry])
        else:
            blanks.append([str(entry)])
    return blanks


def grade_fill_blank(question: Dict[str, Any], user_answer: Any) -> Dict[str, Any]:
    """填空题：逐空比对，可接受同义答案列表，支持按空均分分值。"""
    blanks = _blank_accepted_sets(question)
    total_blanks = len(blanks)

    if isinstance(user_answer, str):
        user_values: List[Any] = [user_answer]
    elif isinstance(user_answer, (list, tuple)):
        user_values = list(user_answer)
    elif user_answer is None:
        user_values = []
    else:
        user_values = [user_answer]

    per_blank = question["points"] / total_blanks
    hits = 0
    details: List[str] = []
    reference: List[str] = []

    for index, accepted in enumerate(blanks):
        reference.append(accepted[0])
        value = user_values[index] if index < len(user_values) else ""
        token = normalize_text(value)
        accepted_norm = {normalize_text(a) for a in accepted}
        if token and token in accepted_norm:
            hits += 1
        elif not token:
            details.append(f"第 {index + 1} 空未作答")
        else:
            details.append(f"第 {index + 1} 空填「{value}」，应为「{accepted[0]}」")

    score = round(per_blank * hits, 2)
    # 避免浮点误差导致满分为 4.9999
    if hits == total_blanks:
        score = question["points"]

    correct = hits == total_blanks
    if correct:
        feedback = f"全部 {total_blanks} 空正确。"
    else:
        feedback = f"答对 {hits}/{total_blanks} 空。" + "；".join(details) + "。"
    return _verdict(score, correct, feedback, user_values, reference=" | ".join(reference))


def grade_short_answer(question: Dict[str, Any], user_answer: Any) -> Dict[str, Any]:
    """简答题：按评分要点（rubric）做关键词命中初筛，并给出相似度提示。

    本函数不负责最终裁定：只要不能确证满分，就标记 ``needs_manual_review``，
    由上层模型结合语义复核后修正得分。
    """
    text = "" if user_answer is None else str(user_answer)
    reference = str(question["answer"])
    rubric = question["grading"].get("rubric") or []
    max_points = question["points"]

    if not normalize_text(text):
        return _verdict(
            0.0,
            False,
            "未作答，未得分。",
            text,
            reference=reference,
            needs_review=False,
        )

    if not rubric:
        # 无评分要点时脚本无法客观判分，全部转人工/模型复核。
        return _verdict(
            0.0,
            False,
            "该题未提供评分要点，需语义复核后给分。",
            text,
            reference=reference,
            needs_review=True,
            extra={"semantic_similarity": round(similarity(text, reference), 3)},
        )

    matched, missing = [], []
    earned = 0.0
    for point in rubric:
        keywords = [str(k) for k in (point.get("keywords") or [])]
        weight = float(point.get("score", 0))
        norm_text = normalize_text(text)
        if keywords and any(normalize_text(k) in norm_text for k in keywords):
            earned += weight
            matched.append(point.get("point", ""))
        else:
            missing.append(point.get("point", ""))

    earned = min(earned, max_points)
    if earned >= max_points:
        earned = max_points
    score = round(earned, 2)
    correct = score >= max_points
    sim = round(similarity(text, reference), 3)

    if correct:
        feedback = "要点齐全，判为满分。"
        needs_review = False
    else:
        missing_text = "；".join(m for m in missing if m) or "（未命中评分要点）"
        feedback = (
            f"命中要点 {len(matched)}/{len(rubric)}，缺少：{missing_text}。"
            f"文本相似度 {sim}（仅供参考）。需语义复核确认最终得分。"
        )
        needs_review = True

    return _verdict(
        score,
        correct,
        feedback,
        text,
        reference=reference,
        needs_review=needs_review,
        extra={
            "semantic_similarity": sim,
            "matched_points": matched,
            "missing_points": [m for m in missing if m],
            "similarity_hint": SHORT_ANSWER_SIMILARITY_HINT,
        },
    )


GRADERS = {
    "single_choice": grade_single_choice,
    "multiple_choice": grade_multiple_choice,
    "true_false": grade_true_false,
    "fill_blank": grade_fill_blank,
    "short_answer": grade_short_answer,
}


def _verdict(
    score: float,
    is_correct: bool,
    feedback: str,
    user_answer: Any,
    *,
    reference: Optional[str] = None,
    needs_review: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造统一的判定结果片段。"""
    payload: Dict[str, Any] = {
        "score": score,
        "is_correct": is_correct,
        "feedback": feedback,
        "user_answer": user_answer,
        "needs_manual_review": needs_review,
    }
    if reference is not None:
        payload["reference_answer"] = reference
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# 汇总与报告
# ---------------------------------------------------------------------------


def grade_quiz(quiz: Dict[str, Any], submission: Dict[str, Any]) -> Dict[str, Any]:
    """执行整卷批改，返回结构化报告。"""
    quiz = normalize_quiz(quiz)
    answers = normalize_submission(submission)

    results: List[Dict[str, Any]] = []
    total_score = 0.0
    max_score = 0.0
    correct_count = 0
    unanswered_count = 0
    by_type: Dict[str, Dict[str, Any]] = {}
    by_knowledge: Dict[str, Dict[str, Any]] = {}

    for question in quiz["questions"]:
        qid = question["id"]
        qtype = question["type"]
        max_points = question["points"]
        max_score += max_points

        raw_answer = answers.get(qid)
        verdict = GRADERS[qtype](question, raw_answer)

        if raw_answer is None or raw_answer == "" or raw_answer == []:
            unanswered_count += 1

        score = float(verdict["score"])
        total_score += score
        if verdict["is_correct"]:
            correct_count += 1

        bucket = by_type.setdefault(
            qtype, {"count": 0, "correct": 0, "score": 0.0, "max_score": 0.0}
        )
        bucket["count"] += 1
        bucket["correct"] += 1 if verdict["is_correct"] else 0
        bucket["score"] = round(bucket["score"] + score, 2)
        bucket["max_score"] = round(bucket["max_score"] + max_points, 2)

        kp = question["knowledge_point"] or "未标注"
        kbucket = by_knowledge.setdefault(kp, {"count": 0, "correct": 0, "score": 0.0, "max_score": 0.0})
        kbucket["count"] += 1
        kbucket["correct"] += 1 if verdict["is_correct"] else 0
        kbucket["score"] = round(kbucket["score"] + score, 2)
        kbucket["max_score"] = round(kbucket["max_score"] + max_points, 2)

        results.append(
            {
                "id": qid,
                "type": qtype,
                "stem": question["stem"],
                "options": question["options"],
                "difficulty": question["difficulty"],
                "cognitive_level": question["cognitive_level"],
                "knowledge_point": question["knowledge_point"],
                "user_answer": verdict["user_answer"],
                "correct_answer": question["answer"],
                "reference_answer": verdict.get("reference_answer"),
                "score": round(score, 2),
                "max_score": max_points,
                "is_correct": verdict["is_correct"],
                "needs_manual_review": verdict["needs_manual_review"],
                "feedback": verdict["feedback"],
                "explanation": question["explanation"],
                "matched_points": verdict.get("matched_points"),
                "missing_points": verdict.get("missing_points"),
                "semantic_similarity": verdict.get("semantic_similarity"),
            }
        )

    percentage = round(total_score / max_score * 100, 2) if max_score else 0.0
    grade, grade_label = resolve_grade(percentage, quiz.get("grade_bands"))

    return {
        "quiz_id": quiz["quiz_id"],
        "title": quiz["title"],
        "topic": quiz.get("topic", ""),
        "difficulty": quiz.get("difficulty", ""),
        "total_score": round(total_score, 2),
        "max_score": round(max_score, 2),
        "percentage": percentage,
        "grade": grade,
        "grade_label": grade_label,
        "passed": percentage >= 60.0,
        "summary": {
            "question_count": len(results),
            "correct": correct_count,
            "wrong": len(results) - correct_count,
            "unanswered": unanswered_count,
            "accuracy": round(correct_count / len(results) * 100, 2) if results else 0.0,
            "needs_manual_review": sum(1 for r in results if r["needs_manual_review"]),
            "by_type": by_type,
            "by_knowledge_point": by_knowledge,
        },
        "results": results,
    }


def build_answer_key_submission(quiz: Dict[str, Any]) -> Dict[str, Any]:
    """由试卷的标准答案反造一份「满分作答」，用于自检。

    填空题取每个空的首个可接受答案；简答题直接使用参考答案全文。
    """
    answers: Dict[str, Any] = {}
    for question in quiz["questions"]:
        qtype, qid = question["type"], question["id"]
        if qtype == "multiple_choice":
            answers[qid] = list(question["answer"])
        elif qtype == "fill_blank":
            raw = question["answer"]
            raw = list(raw) if isinstance(raw, (list, tuple)) else [raw]
            answers[qid] = [b[0] if isinstance(b, (list, tuple, set)) and b else b for b in raw]
        else:
            answers[qid] = question["answer"]
    return {"answers": answers}


def self_check(quiz: Dict[str, Any]) -> Dict[str, Any]:
    """命题自检：用标准答案回灌批改引擎，暴露答案键、评分要点与结构缺陷。

    返回 ``issues`` 列表，``severity`` 为 ``error`` 表示必须修复，``warning`` 表示建议修复。
    """
    quiz = normalize_quiz(quiz)
    issues: List[Dict[str, str]] = []

    for question in quiz["questions"]:
        qid = question["id"]
        if not question["knowledge_point"]:
            issues.append({"id": qid, "severity": "warning", "message": "缺少 knowledge_point，无法按知识点统计薄弱项"})
        if not question["explanation"]:
            issues.append({"id": qid, "severity": "warning", "message": "缺少 explanation，批改反馈将无法给出知识点讲解"})

        if question["type"] == "short_answer":
            rubric = question["grading"].get("rubric") or []
            if not rubric:
                issues.append({"id": qid, "severity": "warning", "message": "简答题缺少 rubric，脚本无法自动判分，将全部转语义复核"})
                continue
            total = sum(float(point.get("score", 0)) for point in rubric)
            if abs(total - question["points"]) > 1e-6:
                issues.append(
                    {
                        "id": qid,
                        "severity": "error",
                        "message": f"rubric 分值合计 {total} 与 points {question['points']} 不一致",
                    }
                )
            reference = normalize_text(question["answer"])
            for point in rubric:
                keywords = [str(k) for k in (point.get("keywords") or [])]
                label = point.get("point", "")
                if not keywords:
                    issues.append({"id": qid, "severity": "warning", "message": f"评分要点「{label}」缺少 keywords"})
                elif not any(normalize_text(k) in reference for k in keywords):
                    issues.append(
                        {
                            "id": qid,
                            "severity": "warning",
                            "message": f"评分要点「{label}」的关键词均未出现在参考答案中，可能造成漏判",
                        }
                    )
        elif question["type"] == "single_choice":
            keys = [str(o.get("key", "")).strip().upper() for o in question["options"]]
            if len(keys) != len(set(keys)):
                issues.append({"id": qid, "severity": "error", "message": "选项 key 存在重复"})
            if _option_key(question, question["answer"]) is None:
                issues.append({"id": qid, "severity": "error", "message": f"标准答案 {question['answer']!r} 不在选项列表中"})
        elif question["type"] == "multiple_choice":
            answer_keys = question["answer"] if isinstance(question["answer"], (list, tuple)) else [question["answer"]]
            if len(answer_keys) < 2:
                issues.append({"id": qid, "severity": "warning", "message": "多选题正确项少于 2 个，与题型定位不符"})

    # 用标准答案回灌，客观题必须满分
    report = grade_quiz(quiz, build_answer_key_submission(quiz))
    for item in report["results"]:
        if item["type"] == "short_answer":
            continue
        if not item["is_correct"] or item["score"] != item["max_score"]:
            issues.append(
                {
                    "id": item["id"],
                    "severity": "error",
                    "message": f"用标准答案自评未得满分：{item['feedback']}",
                }
            )

    errors = [i for i in issues if i["severity"] == "error"]
    return {
        "quiz_id": quiz["quiz_id"],
        "title": quiz["title"],
        "passed": not errors,
        "error_count": len(errors),
        "warning_count": len(issues) - len(errors),
        "objective_full_score_rate": report["percentage"],
        "issues": issues,
    }


def resolve_grade(percentage: float, bands: Optional[Sequence[Sequence[Any]]] = None) -> Tuple[str, str]:
    """按分数档位返回 (等级, 等级说明)。"""
    table = bands or DEFAULT_GRADE_BANDS
    parsed: List[Tuple[float, str, str]] = []
    for row in table:
        if len(row) >= 3:
            parsed.append((float(row[0]), str(row[1]), str(row[2])))
        elif len(row) == 2:
            parsed.append((float(row[0]), str(row[1]), ""))
    parsed.sort(key=lambda item: item[0], reverse=True)
    for threshold, grade, label in parsed:
        if percentage >= threshold:
            return grade, label
    return (parsed[-1][1], parsed[-1][2]) if parsed else ("-", "")


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------


def render_text_report(report: Dict[str, Any]) -> str:
    """把批改报告渲染成便于阅读的纯文本。"""
    lines: List[str] = []
    lines.append(f"== 批改报告：{report['title']} ==")
    lines.append(
        f"总分 {report['total_score']}/{report['max_score']} "
        f"（{report['percentage']}%，等级 {report['grade']} {report['grade_label']}）"
    )
    summary = report["summary"]
    lines.append(
        f"题量 {summary['question_count']}，答对 {summary['correct']}，"
        f"答错 {summary['wrong']}，未作答 {summary['unanswered']}，"
        f"待复核 {summary['needs_manual_review']}"
    )
    lines.append("")
    for item in report["results"]:
        mark = "√" if item["is_correct"] else "×"
        flag = " [待复核]" if item["needs_manual_review"] else ""
        lines.append(
            f"[{mark}] {item['id']}（{item['type']}，{item['score']}/{item['max_score']}分）{flag}"
        )
        lines.append(f"    题干：{item['stem']}")
        if item["options"]:
            lines.append(f"    选项：{_fmt_options({'options': item['options']})}")
        lines.append(f"    作答：{item['user_answer']}")
        lines.append(f"    标准答案：{item['correct_answer']}")
        lines.append(f"    反馈：{item['feedback']}")
        if item["explanation"]:
            lines.append(f"    解析：{item['explanation']}")
        if item["knowledge_point"]:
            lines.append(f"    知识点：{item['knowledge_point']}")
        lines.append("")
    return "\n".join(lines)


def render_self_check_text(payload: Dict[str, Any]) -> str:
    """把命题自检结果渲染成便于阅读的纯文本。"""
    lines = [f"== 命题自检：{payload['title']} =="]
    lines.append(
        f"结论：{'通过' if payload['passed'] else '未通过'}；"
        f"错误 {payload['error_count']} 项，警告 {payload['warning_count']} 项；"
        f"客观题自评得分率 {payload['objective_full_score_rate']}%"
    )
    if not payload["issues"]:
        lines.append("未发现问题。")
    for issue in payload["issues"]:
        tag = "错误" if issue["severity"] == "error" else "警告"
        lines.append(f"  [{tag}] {issue['id']}：{issue['message']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="可批改题目自动评分引擎")
    parser.add_argument("--quiz", required=True, help="试卷 JSON 路径")
    parser.add_argument("--answers", help="作答 JSON 路径（与 --self-check 二选一）")
    parser.add_argument("--self-check", action="store_true", help="命题自检：用标准答案回灌，校验答案键与评分要点")
    parser.add_argument("--output", help="结果输出路径（JSON），省略则打印到标准输出")
    parser.add_argument("--pretty", action="store_true", help="以可读文本而非 JSON 打印到标准输出")
    args = parser.parse_args(argv)

    if args.self_check and args.answers:
        parser.error("--self-check 与 --answers 不能同时使用")
    if not args.self_check and not args.answers:
        parser.error("必须提供 --answers 或 --self-check")

    try:
        quiz = load_json(args.quiz)
        if args.self_check:
            payload: Dict[str, Any] = self_check(quiz)
            exit_code = 0 if payload["passed"] else 1
        else:
            submission = load_json(args.answers)
            payload = grade_quiz(quiz, submission)
            exit_code = 0
    except QuizFormatError as exc:
        print(f"[输入错误] {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[文件错误] {exc}", file=sys.stderr)
        return 2

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"结果已写入 {args.output}")
    elif args.pretty:
        if args.self_check:
            print(render_self_check_text(payload))
        else:
            print(render_text_report(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
