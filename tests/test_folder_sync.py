"""Синк папок №4: ингест форматов, замена версии (2 триггера), two-strike
удаление, рекурсия — всё офлайн (fake httpx / fake листинги).

import app.bitrix_bot исполняет init_db()+load_index() на живых файлах
(идемпотентно; тесты подменяют DB_PATH и пути индекса до любых записей).
"""
import asyncio
import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.course_generator as cg
import app.db as db
import app.index_store as store
import app.rag as rag

QUESTIONS = {"course_summary": "s", "basic_questions": [], "exam_questions": []}

V1_TEXT = " ".join(f"слово{i}" for i in range(500))    # 2 чанка txt (450+50)
V2_TEXT = " ".join(f"слово{i}" for i in range(1000))   # 3 чанка txt


class FakeResponse:
    def __init__(self, json_data=None, content=b""):
        self._json = json_data or {}
        self.content = content
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        pass


class FakeAsyncClient:
    routes: dict = {}
    files: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        for suffix, handler in FakeAsyncClient.routes.items():
            if url.endswith(suffix):
                return FakeResponse(handler(json) if callable(handler) else handler)
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url):
        return FakeResponse(content=FakeAsyncClient.files.get(url, b""))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()

    cp, ep = tmp_path / "chunks.json", tmp_path / "emb.npy"
    cp.write_text("[]", encoding="utf-8")
    np.save(str(ep), np.zeros((0, 4), dtype=np.float32))
    for mod in (rag, store):
        monkeypatch.setattr(mod, "CHUNKS_PATH", str(cp))
        monkeypatch.setattr(mod, "EMBEDDINGS_PATH", str(ep))

    bot._missing_strikes.clear()
    bot._processing.clear()
    bot._recent_msgs.clear()
    bot._ingest_locks.clear()   # asyncio.Lock привязан к лупу — не переживает asyncio.run
    monkeypatch.setattr(bot, "_embed_texts",
                        lambda texts: np.zeros((len(texts), 4), dtype=np.float32))
    monkeypatch.setattr(bot, "_hr_ids", lambda: [])
    monkeypatch.setattr(bot, "_monitored_folders", lambda: {})

    async def fake_send(*args, **kwargs):
        pass

    monkeypatch.setattr(bot, "_send", fake_send)

    calls = {"generate": 0}

    def fake_generate(name, chunks):
        calls["generate"] += 1
        return dict(QUESTIONS, doc_name=name)

    monkeypatch.setattr(cg, "generate_questions", fake_generate)

    FakeAsyncClient.routes = {}
    FakeAsyncClient.files = {}
    monkeypatch.setattr(bot.httpx, "AsyncClient", FakeAsyncClient)
    return calls


def _ingest(file_id, name, folder, content, update_time="T1"):
    FakeAsyncClient.routes["disk.file.get"] = {
        "result": {"DOWNLOAD_URL": f"https://dl/{file_id}",
                   "DETAIL_URL": "https://portal/doc",
                   "UPDATE_TIME": update_time},
    }
    FakeAsyncClient.files[f"https://dl/{file_id}"] = content.encode("utf-8")
    asyncio.run(bot.process_new_document(file_id, name, ["housekeeper"], folder))


# ── Ингест и замена версии ───────────────────────────────────────────────────

def test_ingest_tags_chunks_and_stores_update_time(env):
    _ingest("101", "Правила.txt", "111", V1_TEXT)
    chunks, emb = store.load()
    assert len(chunks) == emb.shape[0] == 2
    assert all(c["doc_name"] == "Правила.txt" and c["folder_id"] == "111"
               and c["roles"] == ["housekeeper"] for c in chunks)
    assert env["generate"] == 1
    assert db.get_processed_file("101")["update_time"] == "T1"


def test_replacement_same_folder(env):
    _ingest("101", "Правила.txt", "111", V1_TEXT, update_time="T1")
    _ingest("102", "Правила.txt", "111", V2_TEXT, update_time="T2")

    chunks, emb = store.load()
    assert len(chunks) == emb.shape[0] == 3            # только v2, старых нет
    assert env["generate"] == 1                        # вопросы НЕ перегенерены
    assert len(db.get_pending_courses()) == 1          # курс один
    assert db.get_processed_file("101") is None        # строка старого file_id снята
    assert db.get_processed_file("102")["update_time"] == "T2"


def test_copy_other_folder_keeps_both(env):
    _ingest("101", "Правила.txt", "111", V1_TEXT)
    _ingest("103", "Правила.txt", "222", V1_TEXT)

    chunks, _ = store.load()
    assert len(chunks) == 4                            # оба набора живут
    assert store.has_document("Правила.txt", "111")
    assert store.has_document("Правила.txt", "222")
    assert env["generate"] == 1                        # курс один (дедуп №1)


def test_reingest_unarchives_course(env):
    _ingest("101", "Правила.txt", "111", V1_TEXT)
    course = db.get_course_by_doc_name("Правила.txt")
    db.set_course_archived(course["id"], True)

    _ingest("102", "Правила.txt", "111", V2_TEXT, update_time="T2")
    assert db.get_course_by_doc_name("Правила.txt")["archived_at"] is None


# ── Синк удалений (two-strike) ───────────────────────────────────────────────

def _seed_deletable(name="Удаляемый.txt", file_id="201", folder="111"):
    store.append_document(
        [{"text": "т" * 60, "heading": name, "section": name,
          "roles": ["housekeeper"], "audience": "staff",
          "doc_name": name, "folder_id": folder}],
        np.zeros((1, 4), dtype=np.float32),
    )
    db.mark_file_processed(file_id, name, folder, "T1")
    cid = db.save_draft_course(name, file_id, json.dumps(QUESTIONS))
    db.activate_course_by_id(cid, "hr")
    return cid


def _listing(tree):
    async def fake_list(client, folder_id, type_):
        result = tree.get((str(folder_id), type_))
        if isinstance(result, Exception):
            raise result
        return result or []
    return fake_list


def test_deletion_two_strike(env, monkeypatch):
    _seed_deletable()
    monkeypatch.setattr(bot, "_list_children", _listing({}))

    asyncio.run(bot._sync_folder("111", ["housekeeper"]))   # strike 1
    assert store.has_document("Удаляемый.txt", "111")
    assert db.get_processed_file("201")

    asyncio.run(bot._sync_folder("111", ["housekeeper"]))   # strike 2 → удаление
    assert not store.has_document("Удаляемый.txt", "111")
    assert db.get_processed_file("201") is None
    assert db.get_active_courses() == []                    # курс архивирован


def test_deletion_spares_copy_in_other_folder(env, monkeypatch):
    _seed_deletable()
    db.mark_file_processed("202", "Удаляемый.txt", "222", "T1")  # копия в другой папке
    monkeypatch.setattr(bot, "_list_children", _listing({}))

    asyncio.run(bot._sync_folder("111", ["housekeeper"]))
    asyncio.run(bot._sync_folder("111", ["housekeeper"]))
    assert db.get_processed_file("201") is None
    assert len(db.get_active_courses()) == 1                # копия держит курс живым


def test_strike_resets_when_file_reappears(env, monkeypatch):
    _seed_deletable()
    monkeypatch.setattr(bot, "_list_children", _listing({}))
    asyncio.run(bot._sync_folder("111", ["housekeeper"]))   # strike 1
    assert bot._missing_strikes

    alive = {("111", "file"): [{"ID": "201", "NAME": "Удаляемый.txt",
                                "UPDATE_TIME": "T1"}]}
    monkeypatch.setattr(bot, "_list_children", _listing(alive))
    asyncio.run(bot._sync_folder("111", ["housekeeper"]))
    assert not bot._missing_strikes
    assert store.has_document("Удаляемый.txt", "111")


def test_listing_error_does_not_strike(env, monkeypatch):
    _seed_deletable()
    monkeypatch.setattr(bot, "_list_children",
                        _listing({("111", "file"): RuntimeError("флап сети")}))
    asyncio.run(bot._sync_folder("111", ["housekeeper"]))
    assert not bot._missing_strikes
    assert store.has_document("Удаляемый.txt", "111")


# ── Триггер (b): новая версия при том же file_id (UPDATE_TIME) ───────────────

def test_update_time_change_triggers_reingest(env, monkeypatch):
    _seed_deletable(name="Обновляемый.txt", file_id="301")
    spawned = []

    async def fake_process(file_id, file_name, roles, folder_id):
        spawned.append(file_id)

    monkeypatch.setattr(bot, "_process_and_release", fake_process)
    tree = {("111", "file"): [{"ID": "301", "NAME": "Обновляемый.txt",
                               "UPDATE_TIME": "T2"}]}  # было T1
    monkeypatch.setattr(bot, "_list_children", _listing(tree))

    asyncio.run(bot._sync_folder("111", ["housekeeper"]))
    assert spawned == ["301"]
    assert db.get_processed_file("301") is None   # гейт снят — реингест пройдёт
    assert not bot._missing_strikes               # файл в листинге — не «удалён»


# ── Рекурсия ─────────────────────────────────────────────────────────────────

def test_walk_recursion_depth_and_role_mapped_skip(env, monkeypatch):
    tree = {
        ("111", "file"): [{"ID": "1", "NAME": "а.txt", "UPDATE_TIME": "T"}],
        ("111", "folder"): [{"ID": "222"}, {"ID": "333"}],
        ("222", "file"): [{"ID": "2", "NAME": "б.txt", "UPDATE_TIME": "T"}],
        ("222", "folder"): [{"ID": "444"}],
        ("333", "file"): [{"ID": "3", "NAME": "в.txt", "UPDATE_TIME": "T"}],
        ("444", "folder"): [{"ID": "555"}],
        ("555", "folder"): [{"ID": "666"}],
        ("666", "folder"): [{"ID": "777"}],   # глубина 6 — не должна посещаться
    }
    monkeypatch.setattr(bot, "_list_children", _listing(tree))
    monkeypatch.setattr(bot, "_monitored_folders",
                        lambda: {"111": ["hk"], "333": ["adm"]})
    spawned = []

    async def fake_process(file_id, file_name, roles, folder_id):
        spawned.append((file_id, tuple(roles), folder_id))

    monkeypatch.setattr(bot, "_process_and_release", fake_process)

    async def run():
        seen, visited = {}, set()
        await bot._walk_folder(None, "111", ["hk"], bot.MAX_FOLDER_DEPTH,
                               seen, visited)
        for _ in range(3):
            await asyncio.sleep(0)
        return seen, visited

    seen, visited = asyncio.run(run())
    assert visited == {"111", "222", "444", "555", "666"}   # 333 скип, 777 глубже лимита
    assert set(seen) == {"1", "2"}                          # файл из 333 не увиден
    assert sorted(s[0] for s in spawned) == ["1", "2"]
    assert all(s[1] == ("hk",) for s in spawned)            # роли корня


# ── №11: скрытые файлы и префиксы департаментов в имени ──────────────────────

def test_hidden_files_skipped_in_walk(env, monkeypatch):
    tree = {("111", "file"): [
        {"ID": "9", "NAME": ".DS_Store", "UPDATE_TIME": "T"},
        {"ID": "10", "NAME": "._FO Внешний вид.docx", "UPDATE_TIME": "T"},
        {"ID": "11", "NAME": "Живой.txt", "UPDATE_TIME": "T"},
    ]}
    monkeypatch.setattr(bot, "_list_children", _listing(tree))
    trashed = []
    FakeAsyncClient.routes["disk.file.markdeleted"] = (
        lambda payload: trashed.append(str(payload["id"])) or {"result": True})
    spawned = []

    async def fake_process(file_id, file_name, roles, folder_id):
        spawned.append(file_id)

    monkeypatch.setattr(bot, "_process_and_release", fake_process)

    async def run():
        seen, visited = {}, set()
        await bot._walk_folder(None, "111", ["hk"], 1, seen, visited)
        for _ in range(6):
            await asyncio.sleep(0)
        return seen

    seen = asyncio.run(run())
    assert set(seen) == {"11"}            # мусор не в seen и не в обработке
    assert spawned == ["11"]
    assert sorted(trashed) == ["10", "9"]  # демо-фидбек E: мусор → корзина Диска


def test_hidden_file_skipped_in_process(env):
    # Путь вебхука: AppleDouble ._*.docx проходит ext-гейт, режется отдельно
    asyncio.run(bot.process_new_document("12", "._Скрытый.docx", ["hk"], "111"))
    assert not db.is_file_processed("12")
    chunks, _ = store.load()
    assert chunks == []


def test_prefix_overrides_folder_roles(env, tmp_path, monkeypatch):
    import app.roles as roles_mod
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"admin_reception": "СПиР", "reservations": "Брони",
                  "all_staff": "Все"},
        "prefixes": {"FO": "admin_reception", "RES": "reservations",
                     "ALL": "all_staff"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles_mod, "CONFIG_PATH", str(cfg))

    _ingest("401", "FO, RES Алгоритм.txt", "111", V1_TEXT)
    chunks, _ = store.load()
    assert chunks and all(
        c["roles"] == ["admin_reception", "reservations"] for c in chunks)
    # Идентичность НЕ меняется: doc_name = полное имя файла с префиксами
    assert all(c["doc_name"] == "FO, RES Алгоритм.txt" for c in chunks)


def test_send_converts_markdown_to_bb(monkeypatch):
    """Протокол 05.08: Bitrix не рендерит markdown — на выходе _send BB-код.
    БЕЗ env-фикстуры: она подменяет _send заглушкой, а тут нужен настоящий."""
    captured = {}
    FakeAsyncClient.routes = {"imbot.message.add": (
        lambda payload: captured.update(payload) or {"result": 1})}
    monkeypatch.setattr(bot.httpx, "AsyncClient", FakeAsyncClient)
    asyncio.run(bot._send("u1", "Напиши *Готов* и жди.", "42", "cid"))
    assert captured["MESSAGE"] == "Напиши [b]Готов[/b] и жди."


# ── Хук: файл документа при старте курса через "/" ───────────────────────────

def test_new_session_triggers_course_file(env, monkeypatch):
    sessions = iter([None, {"course_id": 7}])
    monkeypatch.setattr(bot, "get_session", lambda uid: next(sessions, None))
    monkeypatch.setattr(bot, "process_message",
                        lambda *a, **kw: "ответ")
    sent_files = []

    async def fake_course_file(dialog_id, course_id):
        sent_files.append((dialog_id, course_id))

    monkeypatch.setattr(bot, "_send_course_file", fake_course_file)

    client = TestClient(bot.app)
    client.post("/", data={
        "data[PARAMS][MESSAGE]": "привет",
        "data[PARAMS][DIALOG_ID]": "d1",
        "data[PARAMS][FROM_USER_ID]": "u9",
    })
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not sent_files:
        time.sleep(0.01)
    assert sent_files == [("d1", 7)]


# ── 17.08: дедуп курсов — гонка копий и content-hash ─────────────────────────

def test_parallel_copies_create_one_course(env, monkeypatch):
    """Гонка 29.07 (4 курса «КОНФЕДЕНЦИАЛЬНОСТЬ» за 4с): параллельные копии
    одного документа сериализуются per-doc локом → курс ровно один."""
    def slow_generate(name, chunks):
        time.sleep(0.05)                      # sync, в to_thread — луп живёт
        return dict(QUESTIONS, doc_name=name)

    monkeypatch.setattr(cg, "generate_questions", slow_generate)
    FakeAsyncClient.routes["disk.file.get"] = lambda j: {
        "result": {"DOWNLOAD_URL": f"https://dl/{j['id']}",
                   "DETAIL_URL": "u", "UPDATE_TIME": "T1"}}
    FakeAsyncClient.files["https://dl/g1"] = V1_TEXT.encode("utf-8")
    FakeAsyncClient.files["https://dl/g2"] = V1_TEXT.encode("utf-8")

    async def both():
        await asyncio.gather(
            bot.process_new_document("g1", "Гонка.txt", ["a"], "10"),
            bot.process_new_document("g2", "Гонка.txt", ["b"], "20"))

    asyncio.run(both())
    same_name = [c for c in db.get_pending_courses()
                 if c["doc_name"] == "Гонка.txt"]
    assert len(same_name) == 1


def test_same_content_other_name_attaches(env, monkeypatch):
    """Тот же файл под другим именем: чанки ингестируются (роли), курс НЕ
    создаётся, HR уведомлён (авто-привязка — решение юзера 17.08)."""
    import hashlib

    calls = env
    notified = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        notified.append(text)

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_hr_ids", lambda: ["9"])

    _ingest("h1", "Оригинал.txt", "10", V1_TEXT)
    _ingest("h2", "Копия.txt", "20", V1_TEXT)

    assert calls["generate"] == 1                       # второй генерации нет
    assert db.get_course_by_doc_name("Оригинал.txt")
    assert db.get_course_by_doc_name("Копия.txt") is None
    assert any("то же содержимое" in t for t in notified)
    h = hashlib.sha256(V1_TEXT.encode("utf-8")).hexdigest()
    assert db.get_processed_by_hash(h)["content_hash"] == h
