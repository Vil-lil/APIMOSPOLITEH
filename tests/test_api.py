"""Проверки API: ответы, параметры, ошибки источника и защита от повторных ошибок."""

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from client import ScheduleError
from parser import deadline_kind, parse_grid


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


# Проверки параметров и ошибок источника.

@pytest.fixture
def api_no_network(monkeypatch):
    # Любой забытый сетевой запрос немедленно провалит тест.
    def unexpected_request(*args, **kwargs):
        raise AssertionError("В этом тесте нельзя обращаться к сайту расписания")

    monkeypatch.setattr(main.client, "get_group", unexpected_request)
    with TestClient(main.app) as client:
        yield client


@pytest.mark.parametrize("params", [
    {"from": "02.11.2026"},
    {"to": "02.11.2026"},
    {"date": "31.02.2026"},
    {"from": "01.01.2026", "to": "02.01.2027"},
])
def test_invalid_period_does_not_load_schedule(api_no_network, params):
    response = api_no_network.get("/deadlines/231-321", params=params)
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("params", [
    {"date": "02.11.2026", "format": "xml"},
])
def test_invalid_format_does_not_load_schedule(api_no_network, params):
    response = api_no_network.get("/deadlines/231-321", params=params)
    assert response.status_code == 422


@pytest.mark.parametrize("error,status,message", [
    (ScheduleError("Группа не найдена"), 404, "Группа не найдена"),
    (httpx.ConnectError("Нет соединения"), 502, "rasp.dmami.ru недоступен"),
])
def test_source_errors_become_http_errors(api_no_network, monkeypatch, error, status, message):
    def fail(group, session=False):
        raise error

    monkeypatch.setattr(main.client, "get_group", fail)
    response = api_no_network.get("/schedule/231-321")
    assert response.status_code == status
    assert message in response.json()["detail"]


def test_session_flag_and_exact_date(api_no_network, monkeypatch):
    calls = []

    def load(group, session=False):
        calls.append((group, session))
        return {"grid": {"2026-11-04": {"2": [{
            "sbj": "Химия (практика)",
        }]}}}

    monkeypatch.setattr(main.client, "get_group", load)
    response = api_no_network.get("/deadlines/231-321", params={
        "from": "01.11.2026", "to": "15.11.2026", "session": "true",
    })
    assert response.status_code == 200
    assert calls == [("231-321", True)]
    assert response.json() == [
        {"date": "04.11.2026", "subject": "Химия", "type": "практика"},
    ]


@pytest.mark.parametrize("end,status", [("01.01.2027", 200), ("02.01.2027", 400)])
def test_maximum_period_is_366_days_including_boundaries(api_no_network, monkeypatch, end, status):
    calls = []

    def load(group, session=False):
        calls.append(group)
        return {"grid": {}}

    monkeypatch.setattr(main.client, "get_group", load)
    response = api_no_network.get("/deadlines/231-321", params={"from": "01.01.2026", "to": end})
    assert response.status_code == status
    assert calls == (["231-321"] if status == 200 else [])
    if status == 200:
        assert response.json() == []


def test_next_deadline_when_schedule_is_empty(api_no_network, monkeypatch):
    monkeypatch.setattr(main.client, "get_group", lambda group, session=False: {"grid": {}})
    response = api_no_network.get("/deadlines/231-321/next", params={"start": "2026-11-02"})
    assert response.status_code == 200
    assert response.json() == {
        "deadline": None, "say": "Ближайших лабораторных и практик нет.",
    }


def test_empty_text_response_has_text_content_type(api_no_network, monkeypatch):
    monkeypatch.setattr(main.client, "get_group", lambda group, session=False: {"grid": {}})
    response = api_no_network.get("/deadlines/231-321", params={"date": "02.11.2026", "format": "text"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == ""


# Сценарии, воспроизводившие найденные ошибки.

@pytest.fixture
def api_empty(monkeypatch):
    monkeypatch.setattr(main.client, "get_group", lambda *args: {"grid": {}})
    with TestClient(main.app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize("value", ["2.11.2026", "02.1.2026", " 02.11.2026", "02.11.2026 ", ""])
def test_dates_must_have_exact_documented_format(api_empty, value):
    response = api_empty.get("/deadlines/231-321", params={"date": value})
    assert response.status_code == 400


def test_empty_date_cannot_be_ignored_when_period_is_present(api_empty):
    response = api_empty.get("/deadlines/231-321", params={
        "date": "", "from": "02.11.2026", "to": "08.11.2026",
    })
    assert response.status_code == 400


def test_null_day_means_no_lessons(api_empty, monkeypatch):
    monkeypatch.setattr(main.client, "get_group", lambda *args: {"grid": {"1": None}})
    response = api_empty.get("/schedule/231-321/day", params={"day": "2026-11-02"})
    assert response.status_code == 200
    assert response.json()["lessons"] == []


@pytest.mark.parametrize("payload", [
    {"grid": {"2026-02-30": {"1": [{"sbj": "Физика"}]}}},
    {"grid": {"8": {"1": [{"sbj": "Физика"}]}}},
    {"grid": {"1": {"x": [{"sbj": "Физика"}]}}},
    {"grid": {"1": {"1": [None]}}},
    {"grid": {"1": {"1": [{"sbj": "Физика", "df": "31.02.2026"}]}}},
    {"grid": {"1": {"1": [{"sbj": "Физика", "df": "2026-12-01", "dt": "2026-09-01"}]}}},
    {"grid": {"1": {"1": [{"sbj": ["Физика"]}]}}},
    {"grid": {"1": {"1": [{"sbj": "Физика", "auditories": [{"title": 123}]}]}}},
    {"grid": [1, 2]},
    {"group": "wrong type", "grid": {}},
    [],
])
def test_malformed_source_returns_502_instead_of_500_or_wrong_data(api_empty, monkeypatch, payload):
    monkeypatch.setattr(main.client, "get_group", lambda *args: payload)
    response = api_empty.get("/schedule/231-321")
    assert response.status_code == 502
    assert isinstance(response.json()["detail"], str)


def test_next_deadline_accepts_last_calendar_date(api_empty):
    response = api_empty.get("/deadlines/231-321/next", params={"start": "9999-12-31"})
    assert response.status_code == 200
    assert response.json()["deadline"] is None


@pytest.mark.parametrize("body", [[], "error", {"status": "ok"}, {"status": "ok", "grid": []}])
def test_malformed_client_response_returns_502(api_empty, monkeypatch, body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    with httpx.Client(transport=transport, base_url="https://rasp.dmami.ru") as source:
        monkeypatch.setattr(main.client, "_http", source)
        monkeypatch.setattr(main.client, "_cache", {})
        # Вернуть настоящий метод, подменённый фикстурой api_empty.
        monkeypatch.setattr(main.client, "get_group", main.RaspClient.get_group.__get__(main.client))
        response = api_empty.get("/schedule/231-321")
    assert response.status_code == 502


def test_non_json_source_is_not_reported_as_group_not_found(api_empty, monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>maintenance</html>"))
    with httpx.Client(transport=transport, base_url="https://rasp.dmami.ru") as source:
        monkeypatch.setattr(main.client, "_http", source)
        monkeypatch.setattr(main.client, "_cache", {})
        monkeypatch.setattr(main.client, "get_group", main.RaspClient.get_group.__get__(main.client))
        response = api_empty.get("/schedule/231-321")
    assert response.status_code == 502
