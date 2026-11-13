import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

# 02.09: тесты слали НАСТОЯЩИЕ сообщения в чат HR боевого портала.
# Причина: app.state_machine на импорте зовёт load_dotenv() → боевые
# BITRIX_WEBHOOK_URL/HR_USER_IDS оказываются в env, а notify_hr читает env
# в момент вызова и делает httpx.post. Любой тест, где notify_hr не замокан
# (например test_gamification), доставлял «Сотрудник (ID: u1) …» живым HR.
# Ставим пустые значения ДО импорта app.*: load_dotenv не перезаписывает уже
# существующие ключи, поэтому .env их не вернёт, а notify_hr падает в
# «Config missing — message not sent» вместо сети. Fail-closed.
for _k in ("BITRIX_WEBHOOK_URL", "HR_USER_IDS", "BOT_CLIENT_ID", "HR_CLIENT_ID"):
    os.environ[_k] = ""

# Install before test collection: importing bitrix_bot initializes persistence.
# Real network traffic fails even when a test forgets to mock an API call.
import socket
import tempfile
from pathlib import Path

import numpy as np
import pytest

_SANDBOX = tempfile.TemporaryDirectory(prefix="vertical-tests-")
os.environ["DB_PATH"] = str(Path(_SANDBOX.name) / "bootstrap.db")
os.environ["OPENAI_API_KEY"] = "test-no-network"


def _no_network(*args, **kwargs):
    raise AssertionError("Network access is forbidden in tests; mock the API")


_ORIGINAL_CONNECT = socket.socket.connect


def _guarded_connect(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        return _no_network()
    return _ORIGINAL_CONNECT(sock, address)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect
socket.socket.sendto = _no_network
socket.create_connection = _no_network
socket.getaddrinfo = _no_network

# Redirect index and media paths before modules copy them at import time.
import app.rag as _rag
import app.media_ingest as _media

_rag.CHUNKS_PATH = str(Path(_SANDBOX.name) / "chunks.json")
_rag.EMBEDDINGS_PATH = str(Path(_SANDBOX.name) / "embeddings.npy")
Path(_rag.CHUNKS_PATH).write_text("[]", encoding="utf-8")
np.save(_rag.EMBEDDINGS_PATH, np.empty((0, 0)))
_media.CACHE_PATH = str(Path(_SANDBOX.name) / "media.json")


@pytest.fixture(autouse=True)
def isolated_default_db(tmp_path, monkeypatch):
    import app.db as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "default.db"))
    db.init_db()


@pytest.fixture
def legacy_sessions(monkeypatch):
    """Exercise pre-upgrade sessions in the existing two-stage regressions."""
    import app.db as db
    import app.state_machine as sm
    create = db.create_session

    def legacy_create(*args, **kwargs):
        kwargs.setdefault("quiz_version", 1)
        return create(*args, **kwargs)

    monkeypatch.setattr(db, "create_session", legacy_create)
    monkeypatch.setattr(sm, "create_session", legacy_create)
