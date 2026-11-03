"""Проверка запущенного API. Пример: python check_live_api.py --group 231-321.

Запускать отдельно от pytest, после старта uvicorn. Нужна только стандартная библиотека.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--group", default="231-321")
    parser.add_argument("--other-group", default="261-329")
    parser.add_argument("--output", default="live_api_results.json")
    args = parser.parse_args()
    group = quote(args.group, safe="")
    other = quote(args.other_group, safe="")
    period = {"from": "02.11.2026", "to": "08.11.2026"}
    checks = [
        ("docs", "/docs", {}, (200,)),
        ("openapi", "/openapi.json", {}, (200,)),
        ("schedule", f"/schedule/{group}", {}, (200,)),
        ("day", f"/schedule/{group}/day", {"day": "2026-11-02"}, (200,)),
        ("deadlines", f"/deadlines/{group}", period, (200,)),
        ("text", f"/deadlines/{group}", {**period, "format": "text"}, (200,)),
        ("next", f"/deadlines/{group}/next", {"start": "2026-11-02"}, (200,)),
        ("schedule_other", f"/schedule/{other}", {}, (200,)),
        ("deadlines_other", f"/deadlines/{other}", period, (200,)),
        # Источник может явно сообщить, что расписание сессии отсутствует.
        ("session", f"/schedule/{group}", {"session": "true"}, (200, 404)),
        ("bad_date", f"/deadlines/{group}", {"date": "2.11.2026"}, (400,)),
        ("bad_period", f"/deadlines/{group}", {"from": "08.11.2026", "to": "02.11.2026"}, (400,)),
        ("bad_format", f"/deadlines/{group}", {"date": "02.11.2026", "format": "xml"}, (422,)),
    ]
    results = []
    opener = build_opener(ProxyHandler({}))
    for label, path, params, expected in checks:
        url = args.base_url.rstrip("/") + path + ("?" + urlencode(params) if params else "")
        try:
            response = opener.open(url, timeout=55)
        except HTTPError as error:
            response = error
        except (URLError, OSError) as error:
            results.append({"label": label, "url": url, "status": None,
                            "expected": expected, "ok": False, "body": str(error)})
            print(f"{label}: ошибка соединения: {error}", flush=True)
            break
        with response:
            status = response.code
            raw = response.read().decode("utf-8")
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
            results.append({"label": label, "url": url, "status": status,
                            "expected": expected, "ok": status in expected, "body": body})
        print(f"{label}: HTTP {status}", flush=True)

    # Ответы сохраняются в JSON и при ошибке HTTP, соединения или содержимого.
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_error = None
    if len(results) != len(checks) or not all(row["ok"] for row in results):
        validation_error = "Не все запросы выполнены или HTTP-статус не соответствует ожидаемому."
    else:
        try:
            validate_content(results)
        except (AssertionError, KeyError, TypeError, ValueError, IndexError, StopIteration) as error:
            validation_error = f"Проверка содержимого не пройдена: {error or 'неожиданная структура или содержимое ответа'}"
    print(f"JSON с ответами: {args.output}")
    if validation_error:
        print(validation_error)
        raise SystemExit(1)
    print(f"Успешно: {len(results)} HTTP-проверок и проверки содержимого ответов.")


def validate_content(results):
    bodies = {row["label"]: row["body"] for row in results}
    for label in ("schedule", "schedule_other"):
        assert isinstance(bodies[label]["lessons"], list)
    for label in ("deadlines", "deadlines_other"):
        assert isinstance(bodies[label], list)
        dates = []
        for entry in bodies[label]:
            assert set(entry) == {"date", "subject", "type"}
            day = datetime.strptime(entry["date"], "%d.%m.%Y").date()
            assert datetime(2026, 11, 2).date() <= day <= datetime(2026, 11, 8).date()
            assert entry["type"] in ("практика", "лабораторная работа")
            dates.append(day)
        assert dates == sorted(dates)
    assert bodies["text"] == "\n".join(
        f"{entry['date']};{entry['subject']};{entry['type']}" for entry in bodies["deadlines"]
    )
    assert bodies["day"]["date"] == "2026-11-02"
    assert bodies["day"]["weekday"] == "понедельник"
    assert isinstance(bodies["next"]["say"], str)
    if bodies["deadlines"]:
        nearest = bodies["next"]["deadline"]
        assert nearest is not None
        assert datetime.strptime(bodies["deadlines"][0]["date"], "%d.%m.%Y").date().isoformat() == nearest["date"]
    session = next(row for row in results if row["label"] == "session")
    if session["status"] == 404:
        assert isinstance(session["body"]["detail"], str)
        print("Сессия: источник сообщил об отсутствии расписания; реальные занятия сессии не проверены.")
    else:
        assert isinstance(session["body"]["lessons"], list)


if __name__ == "__main__":
    main()
