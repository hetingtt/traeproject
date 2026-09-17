#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grade_quiz.py 的单元测试。

运行方式：
    python -m unittest discover -s scripts -v
    python scripts/test_grade_quiz.py

覆盖范围：文本归一化、五种题型的判定分支、整卷汇总与等级换算、异常输入。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grade_quiz import (  # noqa: E402
    QuizFormatError,
    build_answer_key_submission,
    grade_fill_blank,
    grade_multiple_choice,
    grade_quiz,
    grade_short_answer,
    grade_single_choice,
    grade_true_false,
    main,
    normalize_quiz,
    normalize_text,
    parse_bool,
    resolve_grade,
    self_check,
    similarity,
)


def make_question(**overrides):
    """构造一道默认合法的单选题，按需覆盖字段。"""
    question = {
        "id": "q1",
        "type": "single_choice",
        "stem": "示例题干",
        "options": [
            {"key": "A", "text": "选项A"},
            {"key": "B", "text": "选项B"},
            {"key": "C", "text": "选项C"},
            {"key": "D", "text": "选项D"},
        ],
        "answer": "B",
        "points": 5,
        "difficulty": "easy",
        "cognitive_level": "remember",
        "knowledge_point": "示例知识点",
        "explanation": "示例解析",
        "grading": {},
    }
    question.update(overrides)
    return question


class TestNormalization(unittest.TestCase):
    def test_normalize_text_strips_punctuation_and_case(self):
        self.assertEqual(normalize_text("  HTTP 协议！ "), "http协议")
        self.assertEqual(normalize_text("HTTP协议"), "http协议")

    def test_normalize_text_handles_fullwidth(self):
        self.assertEqual(normalize_text("ＡＢＣ"), "abc")

    def test_normalize_text_handles_none(self):
        self.assertEqual(normalize_text(None), "")

    def test_parse_bool_variants(self):
        for value in [True, "true", "T", "对", "正确", "√", "是", 1]:
            self.assertIs(parse_bool(value), True, msg=repr(value))
        for value in [False, "false", "F", "错", "错误", "×", "否", 0]:
            self.assertIs(parse_bool(value), False, msg=repr(value))

    def test_parse_bool_invalid(self):
        self.assertIsNone(parse_bool("也许"))

    def test_similarity_bounds(self):
        self.assertEqual(similarity("abc", "abc"), 1.0)
        self.assertEqual(similarity("abc", ""), 0.0)
        self.assertGreater(similarity("TCP协议", "tcp 协议"), 0.9)


class TestSingleChoice(unittest.TestCase):
    def test_correct_answer_full_score(self):
        result = grade_single_choice(make_question(), "B")
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["score"], 5)

    def test_answer_by_option_text(self):
        result = grade_single_choice(make_question(), "选项B")
        self.assertTrue(result["is_correct"])

    def test_wrong_answer_zero_and_explains(self):
        question = make_question(distractor_rationale={"A": "A 是常见混淆项"})
        result = grade_single_choice(question, "A")
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["score"], 0)
        self.assertIn("A 是常见混淆项", result["feedback"])

    def test_unanswered(self):
        result = grade_single_choice(make_question(), "")
        self.assertFalse(result["is_correct"])
        self.assertIn("未作答", result["feedback"])

    def test_invalid_option(self):
        result = grade_single_choice(make_question(), "Z")
        self.assertFalse(result["is_correct"])
        self.assertIn("不是有效选项", result["feedback"])


class TestMultipleChoice(unittest.TestCase):
    def setUp(self):
        self.question = make_question(type="multiple_choice", answer=["A", "C"], points=6)

    def test_exact_match_full_score(self):
        result = grade_multiple_choice(self.question, ["A", "C"])
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["score"], 6)

    def test_order_independent(self):
        result = grade_multiple_choice(self.question, ["C", "A"])
        self.assertTrue(result["is_correct"])

    def test_missing_option_zero_without_partial_credit(self):
        result = grade_multiple_choice(self.question, ["A"])
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["score"], 0)
        self.assertIn("漏选 C", result["feedback"])

    def test_extra_option_zero_without_partial_credit(self):
        result = grade_multiple_choice(self.question, ["A", "B", "C"])
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["score"], 0)
        self.assertIn("多选/错选 B", result["feedback"])

    def test_partial_credit_proportional(self):
        question = make_question(
            type="multiple_choice",
            answer=["A", "B", "C", "D"],
            points=8,
            grading={"partial_credit": True},
        )
        result = grade_multiple_choice(question, ["A", "B"])
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["score"], 4.0)

    def test_partial_credit_never_negative(self):
        question = make_question(
            type="multiple_choice",
            answer=["A"],
            points=4,
            grading={"partial_credit": True},
        )
        result = grade_multiple_choice(question, ["A", "B", "C"])
        self.assertEqual(result["score"], 0.0)

    def test_unanswered(self):
        result = grade_multiple_choice(self.question, [])
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["score"], 0)


class TestTrueFalse(unittest.TestCase):
    def test_correct_true(self):
        result = grade_true_false(make_question(type="true_false", answer=True, points=2), "对")
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["score"], 2)

    def test_correct_false(self):
        result = grade_true_false(make_question(type="true_false", answer=False, points=2), "×")
        self.assertTrue(result["is_correct"])

    def test_wrong(self):
        result = grade_true_false(make_question(type="true_false", answer=True, points=2), False)
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["score"], 0)

    def test_unparsable_answer(self):
        result = grade_true_false(make_question(type="true_false", answer=True, points=2), "不知道")
        self.assertFalse(result["is_correct"])
        self.assertIn("无法识别", result["feedback"])

    def test_bad_reference_answer_raises(self):
        with self.assertRaises(QuizFormatError):
            grade_true_false(make_question(type="true_false", answer="也许"), True)


class TestFillBlank(unittest.TestCase):
    def test_all_blanks_correct_with_synonym(self):
        question = make_question(
            type="fill_blank",
            answer=[["HTTP", "超文本传输协议"], ["80"]],
            points=4,
        )
        result = grade_fill_blank(question, ["超文本传输协议", "80"])
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["score"], 4)

    def test_partial_blanks(self):
        question = make_question(
            type="fill_blank",
            answer=[["HTTP", "超文本传输协议"], ["80"]],
            points=4,
        )
        result = grade_fill_blank(question, ["HTTP", "443"])
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["score"], 2.0)
        self.assertIn("答对 1/2 空", result["feedback"])

    def test_missing_blank_counted_wrong(self):
        question = make_question(type="fill_blank", answer=[["A"], ["B"]], points=4)
        result = grade_fill_blank(question, ["A"])
        self.assertEqual(result["score"], 2.0)
        self.assertIn("第 2 空未作答", result["feedback"])

    def test_single_string_answer(self):
        question = make_question(type="fill_blank", answer="TCP", points=3)
        result = grade_fill_blank(question, "tcp")
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["score"], 3)


class TestShortAnswer(unittest.TestCase):
    def setUp(self):
        self.question = make_question(
            type="short_answer",
            answer="TCP 是面向连接的可靠传输协议，通过三次握手建立连接。",
            points=8,
            grading={
                "rubric": [
                    {"point": "面向连接", "keywords": ["面向连接", "连接"], "score": 4},
                    {"point": "可靠性", "keywords": ["可靠"], "score": 4},
                ]
            },
        )

    def test_full_marks_when_all_points_matched(self):
        result = grade_short_answer(self.question, "TCP 是面向连接的、可靠的传输协议。")
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["score"], 8)
        self.assertFalse(result["needs_manual_review"])

    def test_partial_marks_flagged_for_review(self):
        result = grade_short_answer(self.question, "TCP 提供可靠传输。")
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["score"], 4)
        self.assertTrue(result["needs_manual_review"])
        self.assertIn("面向连接", "；".join(result["missing_points"]))

    def test_no_rubric_requires_review(self):
        question = make_question(type="short_answer", answer="参考答", points=5)
        result = grade_short_answer(question, "随便写点内容")
        self.assertTrue(result["needs_manual_review"])
        self.assertEqual(result["score"], 0)

    def test_blank_answer(self):
        result = grade_short_answer(self.question, "   ")
        self.assertFalse(result["is_correct"])
        self.assertFalse(result["needs_manual_review"])

    def test_score_never_exceeds_max(self):
        question = make_question(
            type="short_answer",
            answer="参考",
            points=5,
            grading={"rubric": [{"point": "a", "keywords": ["a"], "score": 9}]},
        )
        result = grade_short_answer(question, "aaa")
        self.assertEqual(result["score"], 5)


class TestQuizValidation(unittest.TestCase):
    def test_missing_questions_raises(self):
        with self.assertRaises(QuizFormatError):
            normalize_quiz({"quiz_id": "x"})

    def test_bad_type_raises(self):
        with self.assertRaises(QuizFormatError):
            normalize_quiz({"questions": [{"id": "q1", "type": "essay", "stem": "s", "answer": "a"}]})

    def test_duplicate_id_raises(self):
        questions = [
            {"id": "q1", "type": "true_false", "stem": "s", "answer": True},
            {"id": "q1", "type": "true_false", "stem": "s", "answer": False},
        ]
        with self.assertRaises(QuizFormatError):
            normalize_quiz({"questions": questions})

    def test_defaults_are_filled(self):
        quiz = normalize_quiz(
            {"questions": [{"id": "q1", "type": "true_false", "stem": "s", "answer": True}]}
        )
        question = quiz["questions"][0]
        self.assertEqual(question["points"], 1.0)
        self.assertEqual(question["difficulty"], "medium")
        self.assertEqual(quiz["quiz_id"], "quiz")


class TestGradeQuiz(unittest.TestCase):
    def build_quiz(self):
        return {
            "quiz_id": "demo",
            "title": "演示试卷",
            "questions": [
                make_question(id="q1", type="single_choice", answer="B", points=5),
                make_question(id="q2", type="true_false", answer=True, points=5),
                make_question(
                    id="q3",
                    type="fill_blank",
                    answer=[["HTTP"]],
                    points=5,
                    knowledge_point="网络协议",
                ),
                make_question(
                    id="q4",
                    type="short_answer",
                    answer="面向连接的可靠协议",
                    points=5,
                    grading={"rubric": [{"point": "面向连接", "keywords": ["面向连接"], "score": 5}]},
                ),
            ],
        }

    def test_full_marks(self):
        submission = {"answers": {"q1": "B", "q2": "对", "q3": ["HTTP"], "q4": "面向连接的协议"}}
        report = grade_quiz(self.build_quiz(), submission)
        self.assertEqual(report["total_score"], 20.0)
        self.assertEqual(report["max_score"], 20.0)
        self.assertEqual(report["percentage"], 100.0)
        self.assertEqual(report["grade"], "A")
        self.assertTrue(report["passed"])
        self.assertEqual(report["summary"]["correct"], 4)

    def test_partial_and_unanswered(self):
        submission = {"answers": {"q1": "A", "q3": ["FTP"]}}
        report = grade_quiz(self.build_quiz(), submission)
        self.assertEqual(report["total_score"], 0.0)
        self.assertEqual(report["summary"]["correct"], 0)
        self.assertEqual(report["summary"]["unanswered"], 2)
        self.assertFalse(report["passed"])
        self.assertEqual(report["grade"], "F")

    def test_by_type_and_knowledge_aggregation(self):
        submission = {"answers": {"q1": "B", "q2": "错", "q3": ["HTTP"], "q4": "面向连接"}}
        report = grade_quiz(self.build_quiz(), submission)
        by_type = report["summary"]["by_type"]
        self.assertEqual(by_type["single_choice"]["correct"], 1)
        self.assertEqual(by_type["true_false"]["correct"], 0)
        self.assertEqual(by_type["fill_blank"]["score"], 5.0)
        self.assertIn("网络协议", report["summary"]["by_knowledge_point"])

    def test_needs_manual_review_counted(self):
        submission = {"answers": {"q1": "B", "q2": "对", "q3": ["HTTP"], "q4": "连接"}}
        report = grade_quiz(self.build_quiz(), submission)
        self.assertEqual(report["summary"]["needs_manual_review"], 1)

    def test_missing_answers_object_raises(self):
        with self.assertRaises(QuizFormatError):
            grade_quiz(self.build_quiz(), {})


class TestGradeBands(unittest.TestCase):
    def test_default_bands(self):
        self.assertEqual(resolve_grade(95)[0], "A")
        self.assertEqual(resolve_grade(85)[0], "B")
        self.assertEqual(resolve_grade(75)[0], "C")
        self.assertEqual(resolve_grade(60)[0], "D")
        self.assertEqual(resolve_grade(59.9)[0], "F")

    def test_custom_bands(self):
        bands = [[80, "P", "通过"], [0, "N", "未通过"]]
        self.assertEqual(resolve_grade(85, bands)[0], "P")
        self.assertEqual(resolve_grade(20, bands)[0], "N")


class TestSelfCheck(unittest.TestCase):
    def build_quiz(self):
        return {
            "quiz_id": "demo",
            "title": "自检用例",
            "questions": [
                make_question(id="q1", type="single_choice", answer="B", points=5),
                make_question(id="q2", type="multiple_choice", answer=["A", "C"], points=5),
                make_question(id="q3", type="true_false", answer=True, points=5),
                make_question(id="q4", type="fill_blank", answer=[["HTTP", "超文本传输协议"]], points=5),
                make_question(
                    id="q5",
                    type="short_answer",
                    answer="TCP 面向连接且可靠",
                    points=6,
                    grading={
                        "rubric": [
                            {"point": "面向连接", "keywords": ["面向连接"], "score": 3},
                            {"point": "可靠性", "keywords": ["可靠"], "score": 3},
                        ]
                    },
                ),
            ],
        }

    def test_well_formed_quiz_passes(self):
        result = self_check(self.build_quiz())
        self.assertTrue(result["passed"], msg=result["issues"])
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warning_count"], 0)
        self.assertEqual(result["objective_full_score_rate"], 100.0)

    def test_answer_key_submission_reaches_full_marks(self):
        quiz = normalize_quiz(self.build_quiz())
        submission = build_answer_key_submission(quiz)
        report = grade_quiz(quiz, submission)
        self.assertEqual(report["percentage"], 100.0)
        self.assertEqual(report["summary"]["correct"], 5)

    def test_rubric_sum_mismatch_is_error(self):
        quiz = self.build_quiz()
        quiz["questions"][4]["grading"]["rubric"][0]["score"] = 1
        result = self_check(quiz)
        self.assertFalse(result["passed"])
        self.assertTrue(any("rubric 分值合计" in i["message"] for i in result["issues"]))

    def test_answer_not_in_options_is_error(self):
        quiz = self.build_quiz()
        quiz["questions"][0]["answer"] = "Z"
        result = self_check(quiz)
        self.assertFalse(result["passed"])
        self.assertTrue(any("不在选项列表" in i["message"] for i in result["issues"]))

    def test_missing_knowledge_point_is_warning(self):
        quiz = self.build_quiz()
        quiz["questions"][0]["knowledge_point"] = ""
        result = self_check(quiz)
        self.assertTrue(result["passed"])
        self.assertTrue(any("knowledge_point" in i["message"] for i in result["issues"]))

    def test_keyword_absent_from_reference_is_warning(self):
        quiz = self.build_quiz()
        quiz["questions"][4]["grading"]["rubric"][0]["keywords"] = ["握手"]
        result = self_check(quiz)
        self.assertTrue(result["passed"])
        self.assertTrue(any("未出现在参考答案中" in i["message"] for i in result["issues"]))

    def test_empty_accepted_answer_is_error(self):
        quiz = self.build_quiz()
        quiz["questions"][3]["answer"] = [[""]]
        result = self_check(quiz)
        self.assertFalse(result["passed"])
        self.assertTrue(any("未得满分" in i["message"] for i in result["issues"]))


class TestCli(unittest.TestCase):
    """命令行端到端：批改与自检两条路径都要能跑通。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.quiz_path = os.path.join(self.tmp.name, "quiz.json")
        self.answers_path = os.path.join(self.tmp.name, "answers.json")
        quiz = {
            "quiz_id": "cli",
            "title": "CLI 用例",
            "questions": [
                make_question(id="q1", type="single_choice", answer="B", points=5),
                make_question(id="q2", type="true_false", answer=False, points=5),
            ],
        }
        with open(self.quiz_path, "w", encoding="utf-8") as handle:
            json.dump(quiz, handle, ensure_ascii=False)
        with open(self.answers_path, "w", encoding="utf-8") as handle:
            json.dump({"answers": {"q1": "B", "q2": "对"}}, handle, ensure_ascii=False)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        """运行命令行入口并吞掉标准输出，返回退出码。"""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(argv)

    def test_grade_returns_zero(self):
        self.assertEqual(self.run_cli(["--quiz", self.quiz_path, "--answers", self.answers_path]), 0)

    def test_self_check_returns_zero(self):
        self.assertEqual(self.run_cli(["--quiz", self.quiz_path, "--self-check"]), 0)

    def test_pretty_output_contains_report(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            main(["--quiz", self.quiz_path, "--answers", self.answers_path, "--pretty"])
        self.assertIn("批改报告", buffer.getvalue())

    def test_output_file_written(self):
        out = os.path.join(self.tmp.name, "report.json")
        self.assertEqual(
            self.run_cli(["--quiz", self.quiz_path, "--answers", self.answers_path, "--output", out]), 0
        )
        self.assertTrue(os.path.exists(out))

    def test_missing_quiz_file_returns_one(self):
        self.assertEqual(self.run_cli(["--quiz", os.path.join(self.tmp.name, "nope.json"), "--self-check"]), 1)

    def test_requires_answers_or_self_check(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["--quiz", self.quiz_path])


if __name__ == "__main__":
    unittest.main(verbosity=2)
