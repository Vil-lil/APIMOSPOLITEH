"""Тесты parser.py без сети и сторонних библиотек.

Сохраните файл в tests/test_parser.py вашего проекта.
Запуск из корня проекта: python -m unittest discover -s tests -p test_parser.py -v
Также совместим с pytest.
"""

import copy
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser import Lesson, clean_subject, deadline_kind, expand, find_deadlines, parse_grid


def lesson(**changes):
    """Обычная пара по понедельникам; каждый вызов создаёт новый объект."""
    fields = dict(
        subject="Физика", pair=1, start="09:00", end="10:30",
        teacher="Иванов И.И.", rooms=["А-101"], type="Лекция", weekday=1,
    )
    fields.update(changes)
    return Lesson(**fields)


class ParseGridTests(unittest.TestCase):
    def test_empty_schedule(self):
        for payload in ({}, {"grid": None}, {"grid": {}}, {"grid": {"1": []}}):
            with self.subTest(payload=payload):
                self.assertEqual(parse_grid(payload), [])

    def test_regular_lesson_preserves_all_fields(self):
        payload = {"grid": {"2": {"3": [{
            "sbj": "  Физика (лабораторная работа)  ",
            "teacher": "  Иванов И.И.  ", "type": "  Лабораторная  ",
            "df": "2026-09-01", "dt": "31.12.2026",
            "auditories": [{"title": " <a href='#'>А-101</a> "}, {"title": "Б-202"}],
            "e_link": "https://example.org/lesson",
        }]}}}
        parsed = parse_grid(payload)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].to_dict(), {
            "subject": "Физика (лабораторная работа)", "pair": 3,
            "start": "12:20", "end": "13:50", "teacher": "Иванов И.И.",
            "rooms": ["А-101", "Б-202"], "type": "Лабораторная",
            "weekday": 2, "date_from": "2026-09-01", "date_to": "2026-12-31",
            "date": None, "link": "https://example.org/lesson",
        })

    def test_subject_alias_defaults_and_blank_entries(self):
        parsed = parse_grid({"grid": {"1": {"1": [
            {"sbj": ""}, {"sbj": "   "}, {}, {"subject": "  История  "},
        ]}}})
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].subject, "История")
        self.assertEqual(parsed[0].teacher, "")
        self.assertEqual(parsed[0].rooms, [])
        self.assertEqual(parsed[0].type, "")
        self.assertIsNone(parsed[0].link)

    def test_session_uses_exact_date(self):
        parsed = parse_grid({"grid": {"2026-11-03": {"7": [{"sbj": "Экзамен"}]}}})
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].date, date(2026, 11, 3))
        self.assertIsNone(parsed[0].weekday)
        self.assertEqual((parsed[0].start, parsed[0].end), ("19:30", "21:00"))

    def test_parsing_does_not_modify_source(self):
        payload = {"grid": {"1": {"1": [{
            "sbj": " Физика ", "auditories": [{"title": "<b>А-101</b>"}],
        }]}}}
        original = copy.deepcopy(payload)
        parse_grid(payload)
        self.assertEqual(payload, original)


class ExpandTests(unittest.TestCase):
    def test_term_boundaries_are_inclusive(self):
        item = lesson(date_from=date(2026, 11, 2), date_to=date(2026, 11, 16))
        result = expand([item], date(2026, 10, 26), date(2026, 11, 23))
        self.assertEqual([x.date for x in result], [
            date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16),
        ])

    def test_query_boundaries_are_inclusive(self):
        result = expand([lesson()], date(2026, 11, 2), date(2026, 11, 9))
        self.assertEqual([x.date for x in result], [date(2026, 11, 2), date(2026, 11, 9)])

    def test_weekday_alignment_for_every_day(self):
        # Диапазон начинается в среду: понедельник должен попасть в следующую неделю.
        for weekday, expected in enumerate((9, 10, 4, 5, 6, 7, 8), start=1):
            with self.subTest(weekday=weekday):
                result = expand([lesson(weekday=weekday)], date(2026, 11, 4), date(2026, 11, 10))
                self.assertEqual([x.date for x in result], [date(2026, 11, expected)])

    def test_single_day_and_reversed_range(self):
        item = lesson()
        self.assertEqual(len(expand([item], date(2026, 11, 2), date(2026, 11, 2))), 1)
        self.assertEqual(expand([item], date(2026, 11, 3), date(2026, 11, 3)), [])
        self.assertEqual(expand([item], date(2026, 11, 9), date(2026, 11, 2)), [])

    def test_session_does_not_repeat_and_respects_query_boundaries(self):
        items = [lesson(weekday=None, date=date(2026, 11, day)) for day in (1, 2, 9, 10)]
        result = expand(items, date(2026, 11, 2), date(2026, 11, 9))
        self.assertEqual([x.date for x in result], [date(2026, 11, 2), date(2026, 11, 9)])

    def test_sorts_by_date_then_pair(self):
        items = [
            lesson(pair=5, date=date(2026, 11, 3), weekday=None),
            lesson(pair=3), lesson(pair=1),
        ]
        result = expand(items, date(2026, 11, 2), date(2026, 11, 3))
        self.assertEqual([(x.date, x.pair) for x in result], [
            (date(2026, 11, 2), 1), (date(2026, 11, 2), 3), (date(2026, 11, 3), 5),
        ])

    def test_expansion_does_not_set_date_on_original(self):
        item = lesson()
        original = copy.deepcopy(item.to_dict())
        result = expand([item], date(2026, 11, 2), date(2026, 11, 9))
        self.assertEqual(item.to_dict(), original)
        self.assertEqual(len(result), 2)
        self.assertIsNot(result[0], item)
        self.assertIsNot(result[0], result[1])

    def test_leap_day_and_year_transition(self):
        for weekday, start, end, expected in (
            (4, date(2024, 2, 28), date(2024, 3, 1), date(2024, 2, 29)),
            (5, date(2026, 12, 31), date(2027, 1, 2), date(2027, 1, 1)),
        ):
            with self.subTest(expected=expected):
                self.assertEqual([x.date for x in expand([lesson(weekday=weekday)], start, end)], [expected])


    def test_expand_does_not_overflow_after_last_occurrence(self):
        item = Lesson("Физика", 1, "09:00", "10:30", "", [], "Практика", weekday=5)
        result = expand([item], date(9999, 12, 31), date(9999, 12, 31))
        assert [entry.date for entry in result] == [date(9999, 12, 31)]


class DeadlineTests(unittest.TestCase):
    def test_supported_keywords_in_subject_and_type(self):
        cases = (
            ("ЛАБОРАТОРНАЯ РАБОТА", "lab"), ("лаб. раб", "lab"),
            ("лаб.раб", "lab"), ("л/р", "lab"), ("ПРАКТИКА", "practice"),
            ("практическое занятие", "practice"), ("пр. зан", "practice"),
        )
        for keyword, expected in cases:
            with self.subTest(keyword=keyword, source="subject"):
                self.assertEqual(deadline_kind(lesson(subject=f"Физика ({keyword})")), expected)
            with self.subTest(keyword=keyword, source="type"):
                self.assertEqual(deadline_kind(lesson(type=keyword)), expected)

    def test_keyword_in_subject_outside_parentheses_is_not_a_deadline(self):
        for subject in ("Лабораторный практикум", "Практика перевода", "Математика (лекция)"):
            with self.subTest(subject=subject):
                self.assertIsNone(deadline_kind(lesson(subject=subject)))

    def test_clean_subject_removes_work_type_and_normalizes_spaces(self):
        self.assertEqual(clean_subject("  Физика   (лабораторная работа)  "), "Физика")
        self.assertEqual(clean_subject("Высшая математика"), "Высшая математика")

    def test_full_pipeline_filters_lectures_and_returns_serializable_details(self):
        # Вход намеренно не отсортирован; практика закончилась до начала запроса.
        payload = {"grid": {
            "2026-11-04": {"2": [{"sbj": "Химия", "type": "Практика"}]},
            "1": {
                "3": [{"sbj": "Математика", "type": "Лекция"}],
                "1": [{"sbj": "Физика (л/р)", "teacher": "Иванов И.И.",
                       "df": "02.11.2026", "dt": "02.11.2026",
                       "auditories": [{"title": "<b>А-101</b>"}]}],
            },
            "2": {"1": [{"sbj": "Программирование (практика)", "dt": "2026-10-31"}]},
        }}
        result = find_deadlines(parse_grid(payload), date(2026, 11, 2), date(2026, 11, 10))
        self.assertEqual(result, [
            {"subject": "Физика (л/р)", "pair": 1, "start": "09:00", "end": "10:30",
             "teacher": "Иванов И.И.", "rooms": ["А-101"], "type": "", "weekday": 1,
             "date_from": "2026-11-02", "date_to": "2026-11-02", "date": "2026-11-02",
             "link": None, "kind": "lab", "days_left": 0},
            {"subject": "Химия", "pair": 2, "start": "10:40", "end": "12:10",
             "teacher": "", "rooms": [], "type": "Практика", "weekday": None,
             "date_from": None, "date_to": None, "date": "2026-11-04",
             "link": None, "kind": "practice", "days_left": 2},
        ])

    def test_no_deadlines(self):
        for items in ([], [lesson(type="Лекция")]):
            with self.subTest(items=items):
                self.assertEqual(find_deadlines(items, date(2026, 11, 2), date(2026, 11, 9)), [])


    def test_clean_subject_preserves_meaningful_parentheses(self):
        assert clean_subject("Иностранный язык (английский) (практика)") == "Иностранный язык (английский)"
        assert clean_subject("Математика (часть 2)") == "Математика (часть 2)"


if __name__ == "__main__":
    unittest.main(verbosity=2)
