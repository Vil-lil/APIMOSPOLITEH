"""Разбор сетки расписания rasp.dmami.ru и поиск дедлайнов."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

# Время пар Московского Политеха.
PAIR_TIMES = {
    1: ("09:00", "10:30"),
    2: ("10:40", "12:10"),
    3: ("12:20", "13:50"),
    4: ("14:30", "16:00"),
    5: ("16:10", "17:40"),
    6: ("17:50", "19:20"),
    7: ("19:30", "21:00"),
}

# Ключевые фразы в скобках, по которым пара считается дедлайном.
DEADLINE_KEYWORDS = {
    "lab": ("лабораторн", "лаб. раб", "лаб.раб", "л/р"),
    "practice": ("практик", "практическ", "пр. зан"),
}

PARENS_RE = re.compile(r"\(([^()]*)\)")
DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ScheduleDataError(ValueError):
    """Ответ источника содержит некорректную структуру или значения расписания."""


@dataclass
class Lesson:
    subject: str
    pair: int
    start: str
    end: str
    teacher: str
    rooms: list[str]
    type: str
    weekday: int | None = None  # 1 = понедельник (обычное расписание)
    date_from: date | None = None
    date_to: date | None = None
    date: date | None = None  # конкретная дата (сессия или развёрнутое занятие)
    link: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("date_from", "date_to", "date"):
            if d[k]:
                d[k] = d[k].isoformat()
        return d


def deadline_kind(lesson: Lesson) -> str | None:
    """'lab' / 'practice', если в скобках названия (или в типе пары) есть ключевая фраза."""
    sources = [m.lower() for m in PARENS_RE.findall(lesson.subject)]
    sources.append(lesson.type.lower())
    for kind, words in DEADLINE_KEYWORDS.items():
        if any(w in s for s in sources for w in words):
            return kind
    return None


def _parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ScheduleDataError("Дата занятия должна быть строкой")
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value)[:10], fmt).date()
        except ValueError:
            continue
    raise ScheduleDataError(f"Некорректная дата занятия: {value!r}")


def _text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ScheduleDataError("Текстовое поле занятия должно быть строкой")
    return value.strip()


def _rooms(item: dict) -> list[str]:
    rooms = item.get("auditories")
    if rooms is None:
        return []
    if not isinstance(rooms, list):
        raise ScheduleDataError("Список аудиторий должен быть массивом")
    return [re.sub(r"<[^>]+>", "", _text(r.get("title"))).strip()
            for r in rooms if isinstance(r, dict)]


def parse_grid(data: dict) -> list[Lesson]:
    """Плоский список занятий из data['grid'].

    Обычное расписание: grid[день недели][номер пары] -> [занятия].
    Расписание сессии: grid['YYYY-MM-DD'][номер пары] -> [занятия].
    """
    if not isinstance(data, dict):
        raise ScheduleDataError("Расписание должно быть объектом")
    grid = data.get("grid")
    if grid is None:
        return []
    if not isinstance(grid, dict):
        raise ScheduleDataError("Поле grid должно быть объектом")
    lessons = []
    for day_key, pairs in grid.items():
        is_dated = bool(DATE_KEY_RE.fullmatch(str(day_key)))
        concrete_date = _parse_date(day_key) if is_dated else None
        weekday = None
        if not is_dated:
            if str(day_key) not in {str(day) for day in range(1, 8)}:
                raise ScheduleDataError(f"Некорректный день недели: {day_key!r}")
            weekday = int(day_key)
        if pairs is None:
            continue
        if isinstance(pairs, list):  # пустой день иногда приходит списком
            pairs = dict(enumerate(pairs, 1))
        if not isinstance(pairs, dict):
            raise ScheduleDataError("Расписание дня должно быть объектом или массивом")
        for pair_key, items in pairs.items():
            if str(pair_key) not in {str(number) for number in PAIR_TIMES}:
                raise ScheduleDataError(f"Некорректный номер пары: {pair_key!r}")
            pair = int(pair_key)
            start, end = PAIR_TIMES.get(pair, ("", ""))
            if items is None:
                continue
            if not isinstance(items, list):
                raise ScheduleDataError("Занятия пары должны быть массивом")
            for item in items:
                if not isinstance(item, dict):
                    raise ScheduleDataError("Занятие должно быть объектом")
                subject = _text(item.get("sbj") or item.get("subject"))
                if not subject:
                    continue
                date_from = _parse_date(item.get("df"))
                date_to = _parse_date(item.get("dt"))
                if date_from and date_to and date_from > date_to:
                    raise ScheduleDataError("Начало действия занятия позже окончания")
                lessons.append(Lesson(
                    subject=subject,
                    pair=pair,
                    start=start,
                    end=end,
                    teacher=_text(item.get("teacher")),
                    rooms=_rooms(item),
                    type=_text(item.get("type")),
                    weekday=weekday,
                    date_from=date_from,
                    date_to=date_to,
                    date=concrete_date,
                    link=_text(item.get("e_link")) or None,
                ))
    return lessons


def expand(lessons: list[Lesson], start: date, end: date) -> list[Lesson]:
    """Развернуть занятия в конкретные даты в диапазоне [start, end]."""
    result = []
    for l in lessons:
        if l.date:
            if start <= l.date <= end:
                result.append(l)
            continue
        if l.weekday not in range(1, 8):
            raise ScheduleDataError("Для еженедельного занятия нужен день недели от 1 до 7")
        offset = (l.weekday - 1 - start.weekday()) % 7
        if offset > (end - start).days:
            continue
        d = start + timedelta(days=offset)
        while d <= end:
            if (not l.date_from or d >= l.date_from) and (not l.date_to or d <= l.date_to):
                result.append(Lesson(**{**l.__dict__, "date": d}))
            if (end - d).days < 7:
                break
            d += timedelta(days=7)
    result.sort(key=lambda x: (x.date, x.pair))
    return result


def clean_subject(subject: str) -> str:
    """Название предмета без скобок с типом работы: «Физика (лабораторная работа)» -> «Физика»."""
    def remove_work_type(match):
        text = match.group(1).lower()
        is_work_type = any(word in text for words in DEADLINE_KEYWORDS.values() for word in words)
        return "" if is_work_type else match.group(0)

    return re.sub(r"\s+", " ", PARENS_RE.sub(remove_work_type, subject)).strip() or subject


def find_deadlines(lessons: list[Lesson], start: date, end: date) -> list[dict]:
    """Все лабораторные и практики в диапазоне [start, end] с подробностями о паре."""
    out = []
    for l in expand(lessons, start, end):
        kind = deadline_kind(l)
        if kind:
            out.append({**l.to_dict(), "kind": kind, "days_left": (l.date - start).days})
    return out
