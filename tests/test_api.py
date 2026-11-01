from datetime import date

from fastapi.testclient import TestClient

import main
from parser import deadline_kind, parse_grid

# Пример ответа /site/group в формате rasp.dmami.ru с тестовыми данными.
SAMPLE = {
    "status": "ok",
    "group": {"title": "231-321"},
    "grid": {
        "1": {  # понедельник
            "1": [{"sbj": "Физика (лабораторная работа)", "teacher": "Иванов И.И.",
                   "df": "2026-09-01", "dt": "2026-12-31", "type": "",
                   "auditories": [{"title": "<a href='#'>Пр2302</a>"}]}],
            "2": [{"sbj": "Математика", "teacher": "Петров П.П.", "type": "Лекция",
                   "df": "2026-09-01", "dt": "2026-12-31", "auditories": []}],
        },
        "3": {  # среда
            "3": [{"sbj": "Программирование (практика)", "teacher": "", "type": "",
                   "df": "2026-09-01", "dt": "2026-10-31", "auditories": []}],
        },
        "4": {},
    },
}


def test_deadline_kind():
    ls = {l.subject: l for l in parse_grid(SAMPLE)}
    assert deadline_kind(ls["Физика (лабораторная работа)"]) == "lab"
    assert deadline_kind(ls["Программирование (практика)"]) == "practice"
    assert deadline_kind(ls["Математика"]) is None


def _client(monkeypatch):
    monkeypatch.setattr(main.client, "get_group", lambda g, s=False: SAMPLE)
    return TestClient(main.app)


def test_deadlines_period(monkeypatch):
    r = _client(monkeypatch).get("/deadlines/231-321", params={"from": "25.10.2026", "to": "08.11.2026"})
    assert r.status_code == 200
    assert r.json() == [
        {"date": "26.10.2026", "subject": "Физика", "type": "лабораторная работа"},
        {"date": "28.10.2026", "subject": "Программирование", "type": "практика"},
        {"date": "02.11.2026", "subject": "Физика", "type": "лабораторная работа"},
    ]


def test_deadlines_one_day_text(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/deadlines/231-321", params={"date": "26.10.2026", "format": "text"})
    assert r.text == "26.10.2026;Физика;лабораторная работа"
    assert c.get("/deadlines/231-321", params={"date": "27.10.2026"}).json() == []


def test_deadlines_bad_input(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/deadlines/231-321", params={"date": "2026-10-26"}).status_code == 400
    assert c.get("/deadlines/231-321").status_code == 400
    assert c.get("/deadlines/231-321", params={"from": "10.11.2026", "to": "01.11.2026"}).status_code == 400
    assert c.get("/deadlines/231-321", params={"date": "26.10.2026", "from": "26.10.2026"}).status_code == 400


def test_next_and_day(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/deadlines/231-321/next", params={"start": "2026-10-25"}).json()
    assert r["say"].startswith("Лабораторная работа по предмету Физика")
    assert "завтра, в 09:00" in r["say"]
    day = c.get("/schedule/231-321/day", params={"day": "2026-10-26"}).json()
    assert [l["pair"] for l in day["lessons"]] == [1, 2]
