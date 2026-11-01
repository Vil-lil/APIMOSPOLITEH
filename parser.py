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
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value)[:10], fmt).date()
        except ValueError:
            continue
    return None


def _rooms(item: dict) -> list[str]:
    rooms = item.get("auditories") or []
    return [re.sub(r"<[^>]+>", "", r.get("title", "")).strip() for r in rooms if isinstance(r, dict)]


def parse_grid(data: dict) -> list[Lesson]:
    """Плоский список занятий из data['grid'].

    Обычное расписание: grid[день недели][номер пары] -> [занятия].
    Расписание сессии: grid['YYYY-MM-DD'][номер пары] -> [занятия].
    """
    lessons = []
    for day_key, pairs in (data.get("grid") or {}).items():
        is_dated = bool(DATE_KEY_RE.match(str(day_key)))
        if isinstance(pairs, list):  # пустой день иногда приходит списком
            pairs = dict(enumerate(pairs, 1))
        for pair_key, items in pairs.items():
            pair = int(pair_key)
            start, end = PAIR_TIMES.get(pair, ("", ""))
            for item in items or []:
                subject = (item.get("sbj") or item.get("subject") or "").strip()
                if not subject:
                    continue
                lessons.append(Lesson(
                    subject=subject,
                    pair=pair,
                    start=start,
                    end=end,
                    teacher=(item.get("teacher") or "").strip(),
                    rooms=_rooms(item),
                    type=(item.get("type") or "").strip(),
                    weekday=None if is_dated else int(day_key),
                    date_from=_parse_date(item.get("df")),
                    date_to=_parse_date(item.get("dt")),
                    date=_parse_date(day_key) if is_dated else None,
                    link=item.get("e_link") or None,
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
        d = start + timedelta(days=(l.weekday - 1 - start.weekday()) % 7)
        while d <= end:
            if (not l.date_from or d >= l.date_from) and (not l.date_to or d <= l.date_to):
                result.append(Lesson(**{**l.__dict__, "date": d}))
            d += timedelta(days=7)
    result.sort(key=lambda x: (x.date, x.pair))
    return result


def clean_subject(subject: str) -> str:
    """Название предмета без скобок с типом работы: «Физика (лабораторная работа)» -> «Физика»."""
    return re.sub(r"\s+", " ", PARENS_RE.sub("", subject)).strip() or subject


def find_deadlines(lessons: list[Lesson], start: date, end: date) -> list[dict]:
    """Все лабораторные и практики в диапазоне [start, end] с подробностями о паре."""
    out = []
    for l in expand(lessons, start, end):
        kind = deadline_kind(l)
        if kind:
            out.append({**l.to_dict(), "kind": kind, "days_left": (l.date - start).days})
    return out
