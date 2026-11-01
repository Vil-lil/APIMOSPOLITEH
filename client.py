"""Клиент для rasp.dmami.ru (расписание Московского Политеха)."""

import re
import time
from urllib.parse import quote

import httpx

BASE_URL = "https://rasp.dmami.ru"
HEADERS = {
    "Referer": f"{BASE_URL}/",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (POLY smart speaker)",
}
# Сайт иногда отвечает не JSON, а страницей-заглушкой с новым значением cookie bpc.
BPC_RE = re.compile(r"bpc=(\w+)")


class ScheduleError(Exception):
    """Сайт не отдал расписание: группа не найдена или ответ не удалось разобрать."""


class RaspClient:
    """Получает расписание групп с rasp.dmami.ru и кэширует ответы на cache_ttl секунд."""

    def __init__(self, cache_ttl: int = 600, timeout: float = 15.0):
        self._http = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=timeout)
        self._cache: dict[tuple[str, bool], tuple[float, dict]] = {}
        self._cache_ttl = cache_ttl

    def get_group(self, group: str, session: bool = False) -> dict:
        """Сырой JSON расписания группы. session=True — расписание сессии."""
        key = (group, session)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]

        url = f"/site/group?group={quote(group)}&session={int(session)}"
        data = None
        for _ in range(3):
            resp = self._http.get(url)
            resp.raise_for_status()
            try:
                data = resp.json()
                break
            except ValueError:
                match = BPC_RE.search(resp.text)
                if not match:
                    raise ScheduleError("Сайт вернул не JSON и без cookie bpc")
                self._http.cookies.set("bpc", match.group(1), domain="rasp.dmami.ru")
        if data is None:
            raise ScheduleError("Не удалось пройти защиту сайта (bpc)")
        if data.get("status") not in (None, "ok") or "grid" not in data:
            raise ScheduleError(data.get("message") or f"Группа {group} не найдена")

        self._cache[key] = (time.monotonic(), data)
        return data
