"""hr_handler №3: двухшаговая правка, История, Отчёт — TestClient, без сети.

import app.bitrix_bot исполняет init_db()+load_index() на живых файлах (идемпотентно;
живая БД не мутируется — DB_PATH подменяется до любых записей, см. PRP №2).
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.db as db

REPLACEMENT = "Новый вопрос?\nA. а\nB. б\nC. в\nD. г\nОтвет: В"  # В — кириллица = B


def _questions():
    return {
        "doc_name": "Стандарты.docx",
        "course_summary": "О курсе.",
        "basic_questions": [
            {"id": i, "text": f"Базовый {i + 1}?",
             "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
             "correct": "A", "explanation": ""} for i in range(5)
        ],
        "exam_questions": [
            {"id": i, "text": f"Экзамен {i + 1}?",
             "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
             "correct": "A", "explanation": ""} for i in range(10)
        ],
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()

    course_id = db.save_draft_course(
        "Стандарты.docx", "1", json.dumps(_questions(), ensure_ascii=False)
    )
    db.activate_course_by_id(course_id, "hr9")

    monkeypatch.setenv("HR_USER_IDS", "9")
    bot._recent_msgs.clear()
    bot._pending_edits.clear()

    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id=""):
        sent.append((dialog_id, text, bot_id))

    monkeypatch.setattr(bot, "_send", fake_send)
    return TestClient(bot.app), sent, course_id


def _post_and_reply(client, sent, message, from_user="9", dialog="d9"):
    prev = len(sent)
    client.post("/hr", data={
        "data[PARAMS][MESSAGE]": message,
        "data[PARAMS][DIALOG_ID]": dialog,
        "data[PARAMS][FROM_USER_ID]": from_user,
    })
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if len(sent) > prev:
            return sent[-1][1]
        time.sleep(0.01)
    raise AssertionError(f"no reply to {message!r}")


def test_edit_two_step(env):
    client, sent, course_id = env
    reply = _post_and_reply(client, sent, f"Изменить {course_id} 7")
    assert "Экзамен 2?" in reply and "Отмена" in reply

    reply = _post_and_reply(client, sent, REPLACEMENT)
    assert "обновлён" in reply

    questions = db.get_course_questions(course_id)
    assert questions["exam_questions"][1]["text"] == "Новый вопрос?"
    assert questions["exam_questions"][1]["correct"] == "B"  # кириллица нормализована
    assert questions["exam_questions"][0]["text"] == "Экзамен 1?"
    assert questions["basic_questions"][0]["text"] == "Базовый 1?"
    assert db.get_course_by_id(course_id)["approved_at"]     # курс не деактивирован
    assert not bot._pending_edits


def test_edit_bad_format_keeps_pending(env):
    client, sent, course_id = env
    _post_and_reply(client, sent, f"Изменить {course_id} 3")
    reply = _post_and_reply(client, sent, "просто какой-то текст")
    assert "Не понял формат" in reply
    assert bot._pending_edits  # pending жив

    reply = _post_and_reply(client, sent, REPLACEMENT)
    assert "обновлён" in reply
    assert db.get_course_questions(course_id)["basic_questions"][2]["text"] == "Новый вопрос?"


def test_edit_cancel(env):
    client, sent, course_id = env
    _post_and_reply(client, sent, f"Изменить {course_id} 7")
    reply = _post_and_reply(client, sent, "Отмена")
    assert "отменена" in reply
    # pending снят: следующее сообщение — не замена, а неизвестная команда → help
    reply = _post_and_reply(client, sent, REPLACEMENT)
    assert "Доступные команды" in reply
    assert db.get_course_questions(course_id)["exam_questions"][1]["text"] == "Экзамен 2?"


def test_edit_pending_ttl(env):
    client, sent, course_id = env
    _post_and_reply(client, sent, f"Изменить {course_id} 7")
    bot._pending_edits["9"]["expires"] = time.monotonic() - 1
    reply = _post_and_reply(client, sent, REPLACEMENT)
    assert "Доступные команды" in reply  # pending истёк
    assert db.get_course_questions(course_id)["exam_questions"][1]["text"] == "Экзамен 2?"


def test_command_cancels_pending(env):
    client, sent, course_id = env
    _post_and_reply(client, sent, f"Изменить {course_id} 7")
    reply = _post_and_reply(client, sent, "Курсы")
    assert "курс" in reply.lower()
    assert not bot._pending_edits


def test_edit_bad_refs(env):
    client, sent, course_id = env
    assert "не найден" in _post_and_reply(client, sent, "Изменить 999 7")
    assert "от 1" in _post_and_reply(client, sent, f"Изменить {course_id} 16")
    assert "Формат" in _post_and_reply(client, sent, "Изменить")


def test_history_by_email(env):
    client, sent, course_id = env
    db.add_employee("500", "ivan@x.ru", "Иван Иванов", "9")
    session = db.create_session("500", "u500", course_id)
    db.log_answer(session["id"], 0, "basic", "A", 1)
    db.log_answer(session["id"], 1, "exam", "C", 0)

    reply = _post_and_reply(client, sent, "История ivan@x.ru")
    assert "Иван Иванов" in reply
    assert "1. ✓ Базовый 1?" in reply
    assert "7. ✗ Экзамен 2?" in reply
    assert "(верно: A)" in reply


def test_history_unknown_email(env):
    client, sent, _ = env
    reply = _post_and_reply(client, sent, "История ghost@x.ru")
    assert "Пригласить ghost@x.ru" in reply


def test_history_by_uid_without_sessions(env):
    client, sent, _ = env
    reply = _post_and_reply(client, sent, "История 12345")
    assert "нет сессий" in reply


def test_report(env):
    client, sent, course_id = env
    db.add_employee("500", "ivan@x.ru", "Иван Иванов", "9")
    s1 = db.create_session("500", "u500", course_id)
    db.update_session(s1["id"], state="DONE", score_basic=4, score_exam=8)
    s2 = db.create_session("600", "u600", course_id)
    db.update_session(s2["id"], state="WAITING_HR")

    reply = _post_and_reply(client, sent, "Отчёт")
    assert "Иван Иванов (ivan@x.ru)" in reply
    assert "✅ сдан" in reply and "8/10" in reply
    assert "ID 600" in reply and "ждёт допуска" in reply
    # «отчет» без ё тоже работает
    assert "Отчёт по обучению" in _post_and_reply(client, sent, "отчет")


def test_voprosy_through_numbering(env):
    client, sent, course_id = env
    reply = _post_and_reply(client, sent, f"Вопросы {course_id}")
    assert "6. Экзамен 1?" in reply
    assert "15. Экзамен 10?" in reply
    assert f"Изменить {course_id}" in reply
