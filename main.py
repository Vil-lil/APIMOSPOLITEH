"""API расписания Московского Политеха для умной колонки ПОЛИ."""

import re
from datetime import date, datetime, timedelta

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

from client import RaspClient, ScheduleError, ScheduleSourceError
from parser import ScheduleDataError, clean_subject, expand, find_deadlines, parse_grid

app = FastAPI(title="POLY schedule API")
client = RaspClient()

DATE_FORMAT = "%d.%m.%Y"
MAX_PERIOD_DAYS = 366
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
KIND_RU = {"lab": "лабораторная работа", "practice": "практика"}


def _load(group: str, session: bool):
    """Загружает расписание группы с сайта и разбирает его в список занятий."""
    try:
        data = client.get_group(group, session)
        lessons = parse_grid(data)
        group_data = data.get("group")
        if group_data is not None and not isinstance(group_data, dict):
            raise ScheduleDataError("Поле group должно быть объектом")
        if group_data and group_data.get("title") is not None and not isinstance(group_data["title"], str):
            raise ScheduleDataError("Название группы должно быть строкой")
    except (ScheduleSourceError, ScheduleDataError) as e:
        raise HTTPException(502, f"Некорректный ответ rasp.dmami.ru: {e}")
    except ScheduleError as e:
        raise HTTPException(404, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"rasp.dmami.ru недоступен: {e}")
    return data, lessons


def _day(d: date | None) -> date:
    """Возвращает переданную дату или сегодняшнюю."""
    return d or date.today()


def _parse_ddmmyyyy(value: str, name: str) -> date:
    """Разбирает дату строго в формате ДД.ММ.ГГГГ, иначе отвечает ошибкой 400."""
    try:
        if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", value):
            raise ValueError("Нестрогий формат даты")
        return datetime.strptime(value, DATE_FORMAT).date()
    except ValueError:
        raise HTTPException(400, f"Параметр {name}: дата должна быть в формате ДД.ММ.ГГГГ, получено «{value}»")


def _period(day: str | None, date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """Определяет период запроса: один день (date) или диапазон (from и to включительно)."""
    if day is not None and (date_from is not None or date_to is not None):
        raise HTTPException(400, "Укажите либо date, либо from и to, но не всё сразу")
    if day is not None:
        d = _parse_ddmmyyyy(day, "date")
        return d, d
    if date_from is not None and date_to is not None:
        start, end = _parse_ddmmyyyy(date_from, "from"), _parse_ddmmyyyy(date_to, "to")
        if end < start:
            raise HTTPException(400, "Дата to раньше даты from")
        if (end - start).days >= MAX_PERIOD_DAYS:
            raise HTTPException(400, f"Период не может быть длиннее {MAX_PERIOD_DAYS} дней")
        return start, end
    raise HTTPException(400, "Укажите date=ДД.ММ.ГГГГ или from=ДД.ММ.ГГГГ и to=ДД.ММ.ГГГГ")


@app.get("/schedule/{group}")
def schedule(group: str, session: bool = False):
    """Сетка расписания группы как есть (по дням недели или датам сессии)."""
    data, lessons = _load(group, session)
    return {"group": (data.get("group") or {}).get("title") or group,
            "lessons": [l.to_dict() for l in lessons]}


@app.get("/schedule/{group}/day")
def schedule_day(group: str, day: date | None = None, session: bool = False):
    """Пары на конкретный день (по умолчанию сегодня)."""
    d = _day(day)
    _, lessons = _load(group, session)
    return {"date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()],
            "lessons": [l.to_dict() for l in expand(lessons, d, d)]}


@app.get("/deadlines/{group}")
def deadlines(
    group: str,
    day: str | None = Query(None, alias="date", description="Один день, ДД.ММ.ГГГГ"),
    date_from: str | None = Query(None, alias="from", description="Начало периода, ДД.ММ.ГГГГ"),
    date_to: str | None = Query(None, alias="to", description="Конец периода включительно, ДД.ММ.ГГГГ"),
    format: str = Query("json", pattern="^(json|text)$", description="json или text (строки «дата;предмет;тип»)"),
    session: bool = False,
):
    """Дедлайны (лабораторные и практики) за день или период в виде {дата; предмет; тип работы}."""
    start, end = _period(day, date_from, date_to)
    _, lessons = _load(group, session)
    items = [
        {"date": date.fromisoformat(x["date"]).strftime(DATE_FORMAT),
         "subject": clean_subject(x["subject"]),
         "type": KIND_RU[x["kind"]]}
        for x in find_deadlines(lessons, start, end)
    ]
    if format == "text":
        return PlainTextResponse("\n".join(f"{i['date']};{i['subject']};{i['type']}" for i in items))
    return items


@app.get("/deadlines/{group}/next")
def next_deadline(group: str, start: date | None = None):
    """Ближайший дедлайн в компактном виде + готовая фраза для озвучки на ESP32."""
    s = _day(start)
    _, lessons = _load(group, False)
    end = s + timedelta(days=min(60, (date.max - s).days))
    found = find_deadlines(lessons, s, end)
    if not found:
        return {"deadline": None, "say": "Ближайших лабораторных и практик нет."}
    x = found[0]
    when = {0: "сегодня", 1: "завтра"}.get(x["days_left"], f"через {x['days_left']} дн.")
    say = f"{KIND_RU[x['kind']].capitalize()} по предмету {clean_subject(x['subject'])} {when}, в {x['start']}"
    if x["rooms"]:
        say += f", аудитория {x['rooms'][0]}"
    return {"deadline": x, "say": say + "."}
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
