"""Q&A-first (PRPs/qa-first-improvements.md): источник в ответах,
«Мои документы», гейт «Роль», анонс стандарта при загрузке, HR-аббревиатуры,
право смены роли, eval_qa — офлайн, паттерны test_hr_edit_flow/hr_env."""
import asyncio
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.db as db
import app.roles as roles
import app.state_machine as sm

CHUNKS = [
    {"doc_name": "FO Пожар.docx", "text": "т",
     "roles": ["admin_reception"], "audience": "staff"},
    {"doc_name": "ALL Конфиденциальность.docx", "text": "т",
     "roles": ["all_staff"], "audience": "staff"},
    {"doc_name": "ENG Котельная.docx", "text": "т",
     "roles": ["engineer"], "audience": "staff"},
    {"doc_name": "Гостям.docx", "text": "т",
     "roles": ["all_staff"], "audience": "guest"},
]


# ── Источник под RAG-ответом ─────────────────────────────────────────────────

def test_rag_source_line(monkeypatch):
    monkeypatch.setattr(sm, "rag_answer",
                        lambda **kw: ("ОТВЕТ", [{"doc_name": "FO Пожар.docx"}]))
    monkeypatch.setattr(sm, "get_course_by_doc_name",
                        lambda d: {"doc_detail_url": "https://x/doc"})
    out = sm._rag_reply({"role": None}, "вопрос", [], None)
    assert "📄 Источник: Пожар" in out
    assert "https://x/doc" in out


def test_rag_source_absent_on_empty(monkeypatch):
    monkeypatch.setattr(
        sm, "rag_answer",
        lambda **kw: ("Не найдено релевантных фрагментов.", []))
    out = sm._rag_reply({"role": None}, "вопрос", [], None)
    assert "Источник" not in out


# ── «Мои документы» ──────────────────────────────────────────────────────────

def test_my_documents_filtering(monkeypatch):
    monkeypatch.setattr(sm, "get_course_by_doc_name", lambda d: None)
    fo = sm._my_documents_text("admin_reception", CHUNKS)
    assert "Пожар" in fo and "Конфиденциальность" in fo
    assert "Котельная" not in fo and "Гостям" not in fo
    none = sm._my_documents_text(None, CHUNKS)
    assert "Конфиденциальность" in none and "Пожар" not in none
    assert "выбери роль" in none
    assert "нет документов" in sm._my_documents_text("engineer", [CHUNKS[0]])


# ── Гейт «Роль» ──────────────────────────────────────────────────────────────

def test_role_gate(monkeypatch):
    session = {"id": 1, "user_id": "u1", "state": "READING",
               "role": "fo", "course_id": 1}
    monkeypatch.setattr(sm, "_start_role_select", lambda: "МЕНЮ РОЛЕЙ")
    monkeypatch.setattr(sm, "update_session", lambda sid, **kw: None)
    # запрет + отдел замаплен → отказ
    monkeypatch.setattr(sm, "get_employee", lambda uid: {"can_switch_role": 0})
    monkeypatch.setattr(sm, "_role_from_profile",
                        lambda uid: "admin_reception")
    assert "🔒" in sm._handle_reading(session, "Роль", [], None)
    # право есть → меню
    monkeypatch.setattr(sm, "get_employee", lambda uid: {"can_switch_role": 1})
    assert sm._handle_reading(session, "Роль", [], None) == "МЕНЮ РОЛЕЙ"
    # отдел НЕ замаплен → гейт неактивен даже при запрете
    monkeypatch.setattr(sm, "get_employee", lambda uid: {"can_switch_role": 0})
    monkeypatch.setattr(sm, "_role_from_profile", lambda uid: None)
    assert sm._handle_reading(session, "Роль", [], None) == "МЕНЮ РОЛЕЙ"


# ── Анонс стандарта при загрузке ─────────────────────────────────────────────

def test_standard_recipients(monkeypatch):
    monkeypatch.setattr(bot, "get_all_employees", lambda: [
        {"bitrix_uid": "1"}, {"bitrix_uid": "2"}, {"bitrix_uid": "3"}])
    roles_by = {"1": "admin_reception", "2": "engineer", "3": None}
    monkeypatch.setattr(bot, "_last_known_role", lambda uid: roles_by[uid])
    assert bot._standard_recipients(["admin_reception"]) == ["1"]
    assert set(bot._standard_recipients(["all_staff"])) == {"1", "2", "3"}


def test_broadcast_new_standard(monkeypatch):
    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        sent.append((dialog_id, text))

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_standard_recipients", lambda r: ["7"])
    asyncio.run(bot._broadcast_new_standard(
        "FO Пожар.docx", "https://x", ["admin_reception"]))
    assert sent and sent[0][0] == "u7"
    assert "Новый стандарт доступен" in sent[0][1]
    assert "Тест будет назначен позже" in sent[0][1]
    assert "https://x" in sent[0][1]


def test_course_announce_mentions_deadline(monkeypatch):
    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        sent.append(text)

    monkeypatch.setattr(bot, "_send", fake_send)
    asyncio.run(bot._broadcast_course({"doc_name": "Д.docx"}, ["1"]))
    assert f"{bot.ESCALATION_DAYS} дней" in sent[0]


# ── HR: аббревиатуры и право смены роли ──────────────────────────────────────

@pytest.fixture
def hr_env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"admin_reception": "СПиР", "all_staff": "Все"},
        "prefixes": {"FO": "admin_reception"},
        "folders": {}, "departments": {}}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))
    monkeypatch.setenv("HR_USER_IDS", "9")
    bot._recent_msgs.clear()
    bot._pending_edits.clear()
    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        sent.append((dialog_id, text))

    monkeypatch.setattr(bot, "_send", fake_send)
    return TestClient(bot.app), sent


def _post_and_reply(client, sent, message):
    prev = len(sent)
    client.post("/hr", data={
        "data[PARAMS][MESSAGE]": message,
        "data[PARAMS][DIALOG_ID]": "d9",
        "data[PARAMS][FROM_USER_ID]": "9",
    })
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if len(sent) > prev:
            return sent[-1][1]
        time.sleep(0.01)
    raise AssertionError(f"no reply to {message!r}")


def test_abbrev_add_list_remove(hr_env):
    client, sent = hr_env
    reply = _post_and_reply(client, sent, "Аббревиатуры")
    assert "FO → СПиР" in reply
    reply = _post_and_reply(client, sent,
                            "Аббревиатура добавить FIN Финансовая служба")
    assert "✅ FIN → Финансовая служба" in reply
    cfg = roles.load_roles_config()
    assert cfg["prefixes"]["FIN"] == "fin"
    assert cfg["roles"]["fin"] == "Финансовая служба"
    assert "уже занята" in _post_and_reply(
        client, sent, "Аббревиатура добавить FO Другое")
    assert "латиница" in _post_and_reply(
        client, sent, "Аббревиатура добавить ЖЖЖ Тест")
    assert "укажи название" in _post_and_reply(
        client, sent, "Аббревиатура добавить SPA").lower()
    reply = _post_and_reply(client, sent, "Аббревиатура удалить FIN")
    assert "удалена" in reply
    assert "FIN" not in roles.load_roles_config()["prefixes"]


def test_role_allow_deny(hr_env):
    client, sent = hr_env
    db.add_employee("500", "ivan@x.ru", "Иван", "9")
    reply = _post_and_reply(client, sent, "Роль запретить ivan@x.ru")
    assert "больше не выбирает" in reply
    assert db.get_employee("500")["can_switch_role"] == 0
    reply = _post_and_reply(client, sent, "Роль разрешить 500")
    assert "может выбирать" in reply
    assert db.get_employee("500")["can_switch_role"] == 1
    assert "не найден" in _post_and_reply(client, sent,
                                          "Роль запретить ghost@x.ru")


# ── eval_qa ──────────────────────────────────────────────────────────────────

def test_eval_qa_report(tmp_path, monkeypatch):
    import eval_qa as ev
    monkeypatch.setattr(ev, "load_index", lambda: ([
        {"doc_name": "FO Пожар.docx", "text": "слово " * 100},
        {"doc_name": "FO, RES Карт.docx", "text": "пять слов всего тут"},
    ], None))
    monkeypatch.setattr(
        ev, "_llm_json",
        lambda client, s, u, max_tokens, validate=None: {"questions": ["В1?", "В2?"]})
    monkeypatch.setattr(
        ev, "rag_answer",
        lambda **kw: ("ОТВЕТ БОТА", [{"doc_name": "FO Пожар.docx"}]))
    monkeypatch.setattr(ev, "OpenAI", lambda **kw: object())
    monkeypatch.setattr(sys, "argv", ["eval_qa", "--date", "20990101",
                                      "--questions", "2",
                                      "--out", str(tmp_path)])
    ev.main()
    report = (tmp_path / "eval_qa_20990101.md").read_text(encoding="utf-8")
    assert "В1?" in report and "ОТВЕТ БОТА" in report
    assert "бот по нему слеп" in report                  # документ-картинка
    assert "Источник top-1: FO Пожар.docx" in report
