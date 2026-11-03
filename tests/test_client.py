"""Проверки HTTP-клиента на управляемом транспорте без настоящего сайта."""

import httpx
import pytest

import client as module
from client import RaspClient, ScheduleError, ScheduleSourceError


@pytest.fixture
def make_client():
    clients = []

    def make(handler, **kwargs):
        rasp = RaspClient(**kwargs)
        rasp._http.close()
        rasp._http = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://rasp.dmami.ru",
        )
        clients.append(rasp)
        return rasp

    yield make
    for rasp in clients:
        rasp._http.close()


def test_cache_hit_does_not_repeat_request(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"grid": {}, "group": {"title": "231-321"}})

    rasp = make_client(handler)
    assert rasp.get_group("231-321") == rasp.get_group("231-321")
    assert len(calls) == 1


def test_session_has_its_own_cache_entry(make_client):
    sessions = []

    def handler(request):
        sessions.append(request.url.params["session"])
        return httpx.Response(200, json={"grid": {}})

    rasp = make_client(handler)
    for session in (False, True, False, True):
        rasp.get_group("231-321", session)
    assert sessions == ["0", "1"]


def test_cache_expires_at_ttl_boundary(make_client, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"grid": {}, "revision": len(calls)})

    rasp = make_client(handler, cache_ttl=10)
    assert rasp.get_group("231-321")["revision"] == 1
    clock[0] = 9.9
    assert rasp.get_group("231-321")["revision"] == 1
    clock[0] = 10
    assert rasp.get_group("231-321")["revision"] == 2


def test_oldest_entry_is_evicted_when_cache_is_full(make_client):
    groups = []

    def handler(request):
        groups.append(request.url.params["group"])
        return httpx.Response(200, json={"grid": {}})

    rasp = make_client(handler, max_cache_entries=2)
    for group in ("a", "b", "c", "b", "a"):
        rasp.get_group(group)
    assert groups == ["a", "b", "c", "a"]


def test_expired_entries_are_removed_on_other_group_request(make_client, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    rasp = make_client(lambda request: httpx.Response(200, json={"grid": {}}), cache_ttl=10)
    rasp.get_group("old")
    clock[0] = 10
    rasp.get_group("new")
    assert set(rasp._cache) == {("new", False)}


def test_cookie_challenge_is_retried(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, text="document.cookie = 'bpc=abc123'")
        assert "bpc=abc123" in request.headers.get("cookie", "")
        return httpx.Response(200, json={"grid": {}})

    assert make_client(handler).get_group("231-321") == {"grid": {}}
    assert len(calls) == 2


def test_cookie_retry_is_limited_to_three_attempts(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="bpc=abc123")

    with pytest.raises(ScheduleSourceError):
        make_client(handler).get_group("231-321")
    assert len(calls) == 3


def test_missing_group_error_is_not_cached(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"status": "error", "message": "Группа не найдена"})
        return httpx.Response(200, json={"grid": {}})

    rasp = make_client(handler)
    with pytest.raises(ScheduleError, match="Группа не найдена"):
        rasp.get_group("231-321")
    assert rasp.get_group("231-321") == {"grid": {}}
    assert len(calls) == 2


def test_network_timeout_is_preserved(make_client):
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(httpx.ReadTimeout):
        make_client(handler).get_group("231-321")


def test_http_error_is_preserved(make_client):
    with pytest.raises(httpx.HTTPStatusError):
        make_client(lambda request: httpx.Response(503)).get_group("231-321")


def test_group_is_encoded_as_single_query_value(make_client):
    group = "231-321 &session=1/тест"

    def handler(request):
        assert request.url.params.multi_items() == [("group", group), ("session", "0")]
        return httpx.Response(200, json={"grid": {}})

    assert make_client(handler).get_group(group) == {"grid": {}}


@pytest.mark.parametrize("kwargs", [{"cache_ttl": -1}, {"max_cache_entries": 0}])
def test_invalid_cache_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        RaspClient(**kwargs)
