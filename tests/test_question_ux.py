"""17.08: карточный просмотр «Вопросы N.q» и перегенерация — чистые функции,
роутинг, фоновая задача (LLM мокнут). Паттерны — test_buttons_everywhere."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.course_generator as cg
import app.hr_tools as ht


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _q(i=0, text=None):
    return {"id": i, "text": text or f"Вопрос {i}?",
            "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
            "correct": "A", "explanation": ""}


def _questions():
    return {
        "doc_name": "Д.docx", "course_summary": "s",
        "basic_questions": [_q(i) for i in range(5)],
        "exam_questions": [_q(i) for i in range(10)],
        "facts_basic": [f"факт {i}" for i in range(5)],
        "facts_exam": [f"положение {i}" for i in range(10)],
    }


# ── Чистые функции ───────────────────────────────────────────────────────────

def test_format_question_card():
    q = {"text": "Куда звонить?", "options": ["A. 112", "B. 01", "C. 02", "D. 03"],
         "correct": "B", "explanation": "по стандарту"}
    card = ht.format_question_card("Пожар.docx", q, 2)
    assert "📋 *Пожар.docx*" in card
    assert "Базовый блок — вопрос 2/5" in card
    assert "2. Куда звонить?" in card
    assert "B. 01 ✅" in card and "A. 112\n" in card
    assert "Пояснение: по стандарту" in card
    # экзаменационная нумерация
    assert "Экзамен — вопрос 2/10" in ht.format_question_card("Д", q, 7)


# ── Роутинг «Вопросы N.q» ────────────────────────────────────────────────────

@pytest.fixture
def hr_env(monkeypatch):
    captured = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        captured.append({"dialog": dialog_id, "text": text,
                         "keyboard": keyboard})

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setenv("HR_USER_IDS", "")
    bot._recent_msgs.clear()
    bot._pending_edits.clear()
    return TestClient(bot.app), captured


def _post_hr(client, message, user="hr1", dialog="dhr"):
    return client.post("/hr", data={
        "data[PARAMS][MESSAGE]": message,
        "data[PARAMS][DIALOG_ID]": dialog,
        "data[PARAMS][FROM_USER_ID]": user,
    })


def test_questions_card_navigation(hr_env, monkeypatch):
    monkeypatch.setattr(bot, "get_course_by_id",
                        lambda cid: {"id": cid, "doc_name": "Д.docx"})
    monkeypatch.setattr(bot, "get_course_questions", lambda cid: _questions())
    client, captured = hr_env
    _post_hr(client, "Вопросы 5.7")
    assert _wait_for(lambda: captured)
    assert "Экзамен — вопрос 2/10" in captured[0]["text"]
    assert "7. Вопрос 1?" in captured[0]["text"]
    _post_hr(client, "Вопросы 5.16")
    assert _wait_for(lambda: len(captured) >= 2)
    assert "от 1 до 15" in captured[1]["text"]


# ── Перегенерация ────────────────────────────────────────────────────────────
# Фоновая корутина зовётся напрямую (asyncio.run): задача с to_thread не
# доживает в пер-запросном лупе TestClient — паттерн test_report_delivery.

_DOC_CHUNKS = [{"doc_name": "Д.docx", "heading": "h", "text": "т"}]
_COURSE = {"id": 5, "doc_name": "Д.docx"}


def test_regen_command_starts_task(hr_env, monkeypatch):
    monkeypatch.setattr(bot, "get_course_by_id",
                        lambda cid: {"id": cid, "doc_name": "Д.docx"})
    monkeypatch.setattr(bot, "chunks", list(_DOC_CHUNKS))
    started = []

    async def fake_task(dialog_id, client_id, course, q_num, doc_chunks):
        started.append((course["id"], q_num, len(doc_chunks)))

    monkeypatch.setattr(bot, "_regenerate_and_notify", fake_task)
    client, captured = hr_env
    _post_hr(client, "Перегенерировать 5.7")
    assert _wait_for(lambda: captured and started)
    assert captured[0]["text"].startswith("⏳")
    assert started == [(5, 7, 1)]


@pytest.fixture
def send_trap(monkeypatch):
    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        sent.append({"text": text, "keyboard": keyboard})

    monkeypatch.setattr(bot, "_send", fake_send)
    return sent


def test_regen_one_flow(send_trap, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    monkeypatch.setattr(bot, "get_course_questions", lambda cid: _questions())
    saved = {}
    monkeypatch.setattr(bot, "update_course_questions",
                        lambda cid, qjson: saved.update(q=json.loads(qjson)))
    regen_args = {}

    def fake_regen(doc_name, chunks, fact, existing):
        regen_args.update(fact=fact, n_existing=len(existing))
        return {"text": "Свежий?", "options": ["A. х", "B. у", "C. z", "D. w"],
                "correct": "A", "explanation": ""}

    monkeypatch.setattr(cg, "regenerate_one", fake_regen)
    asyncio.run(bot._regenerate_and_notify("d9", "", _COURSE, 7, _DOC_CHUNKS))

    assert "пересоздан" in send_trap[0]["text"]
    assert "Свежий?" in send_trap[0]["text"]
    assert regen_args["fact"] == "положение 1"                  # №7 = exam[1]
    assert regen_args["n_existing"] == 15
    assert saved["q"]["exam_questions"][1]["text"] == "Свежий?"
    assert saved["q"]["exam_questions"][1]["id"] == 1           # id прежний
    texts = [b["TEXT"] for b in send_trap[0]["keyboard"] if "TEXT" in b]
    assert "▶️ Далее" in texts                                   # карточная


def test_regen_whole_course(send_trap, monkeypatch):
    saved = {}
    monkeypatch.setattr(bot, "update_course_questions",
                        lambda cid, qjson: saved.update(q=json.loads(qjson)))
    monkeypatch.setattr(cg, "generate_questions",
                        lambda name, chunks: _questions())
    asyncio.run(bot._regenerate_and_notify("d9", "", _COURSE, None, _DOC_CHUNKS))
    assert "затёрты" in send_trap[0]["text"]
    assert len(saved["q"]["exam_questions"]) == 10


def test_regen_without_chunks_refuses(hr_env, monkeypatch):
    monkeypatch.setattr(bot, "get_course_by_id",
                        lambda cid: {"id": cid, "doc_name": "Д.docx"})
    monkeypatch.setattr(bot, "chunks", [{"heading": "h", "text": "т"}])  # без doc_name
    client, captured = hr_env
    _post_hr(client, "Перегенерировать 5.1")
    assert _wait_for(lambda: captured)
    assert "не найдены в индексе" in captured[0]["text"]


def test_regen_failure_reports(send_trap, monkeypatch):
    monkeypatch.setattr(bot, "get_course_questions", lambda cid: _questions())

    def boom(*a, **kw):
        raise ValueError("LLM сломался")

    monkeypatch.setattr(cg, "regenerate_one", boom)
    asyncio.run(bot._regenerate_and_notify("d9", "", _COURSE, 1, _DOC_CHUNKS))
    assert "❌" in send_trap[0]["text"]
