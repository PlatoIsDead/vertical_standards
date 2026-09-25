"""Хост ссылок на документы в сообщениях сотрудникам.

В БД ссылка хранится с хостом из BITRIX_WEBHOOK_URL (portal.becar.ru, 21.08).
Мобильное приложение Битрикса, похоже, живёт на другом домене портала
(propertyhunters.pro) — DOC_LINK_HOST подменяет хост в момент отправки,
не трогая БД. Пусто — ссылка как в БД.
"""
import os
from urllib.parse import urlsplit, urlunsplit


def doc_url(url: str | None, host: str | None = None) -> str | None:
    host = host if host is not None else os.getenv("DOC_LINK_HOST", "").strip()
    if not url or not host:
        return url
    parts = urlsplit(url)
    if not parts.netloc:
        return url
    return urlunsplit((parts.scheme or "https", host, parts.path,
                       parts.query, parts.fragment))
