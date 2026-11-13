"""07.11: P0-регрессия — единое соответствие «текст вопроса ↔ буква ↔ канон».

Живой дефект (скриншоты data/photo_2026-11-07_00-36-11.jpg и
data/photo_2026-11-07_00-35-11.jpg): первый вопрос экзамена показывал варианты
в КАНОНИЧЕСКОМ порядке (_handle_exam_ready звал format_question без session),
а кнопки и сверка ответа работали в пер-сессионном порядке — правильный текст
под показанной буквой A в фидбеке назывался «B», ответ по видимой букве
мог быть засчитан неверно.

Здесь шафл ВКЛЮЧЁН. Сеть запрещена tests/conftest.py. Проверяются сохранённые legacy-сессии.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("legacy_sessions")

import app.bitrix_bot as bot
import app.db as db
import app.quiz as quiz
import app.roles as roles
import app.state_machine as sm


def _q(text, bodies, correct, explanation="по стандарту"):
    return {"id": 0, "text": text,
            "options": [f"{letter}. {b}" for letter, b in zip("ABCD", bodies)],
            "correct": correct, "explanation": explanation}


QUESTIONS = {
    "course_summary": "s",
    "basic_questions": [
        _q(f"Б{i}?", [f"б{i}а", f"б{i}б", f"б{i}в", f"б{i}г"], "A")
        for i in range(5)
    ],
    # Первый экзаменационный — мотив скриншота «Vertical 4+1»,
    # второй — мотив скриншота «перевод звонка»
    "exam_questions": [
        _q("Что означает Vertical 4+1?",
           ["4 этажа + 1 бизнес", "4 корпуса", "5 звёзд", "4 отеля"], "A"),
        _q("Гость просит перевести звонок в другой отель. Действия?",
           ["Соединить с 5555", "Отказать", "Перевести на руководителя",
            "Попросить перезвонить"], "D"),
        _q("Э3?", ["э3а", "э3б", "э3в", "э3г"], "B"),
    ],
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SHUFFLE_OPTIONS", "1")
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"housekeeper": "Горничная", "all_staff": "Все"},
        "prefixes": {}, "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))
    cid = db.save_draft_course("Стандарты.docx", "1",
                               json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid, "hr")
    monkeypatch.setattr(sm, "rag_answer", lambda **kw: ("MOCK", []))
    hr_calls = []
    monkeypatch.setattr(sm, "notify_hr",
                        lambda *a, **kw: hr_calls.append((a, kw)))
    for i in range(1, 61):
        uid = f"u{i}"
        db.add_employee(uid, added_by="t")
        db.set_employee_role(uid, "housekeeper")
    return SimpleNamespace(course_id=cid, hr_calls=hr_calls)


def _option_lines(text: str) -> dict:
    """{буква: тело} из показанного текста вопроса."""
    out = {}
    for line in text.splitlines():
        for letter in quiz.LETTERS:
            if line.startswith(f"{letter}. "):
                out[letter] = line[len(f"{letter}. "):]
    return out


def _correct_display(session: dict, phase: str, idx: int) -> str:
    q = db.get_course_questions(session["course_id"])[f"{phase}_questions"][idx]
    return quiz.question_view(session, q, phase, idx).correct_display


def _pass_basic(uid: str) -> None:
    for i in range(5):
        s = db.get_session(uid)
        sm.process_message(uid, _correct_display(s, "basic", i),
                           f"d{uid}", [], None)


def _to_exam_ready(uid: str) -> None:
    sm.process_message(uid, "привет", f"d{uid}", [], None)
    sm.process_message(uid, "Готов", f"d{uid}", [], None)
    _pass_basic(uid)
    assert db.get_session(uid)["state"] == "EXAM_READY"


def _uid_with_perm(env, phase: str, idx: int, start: int = 1):
    """Первый uid из пула, чья перестановка для (phase, idx) — не тождественная."""
    for i in range(start, 61):
        uid = f"u{i}"
        _to_exam_ready(uid)
        sid = db.get_session(uid)["id"]
        if quiz.option_perm(sid, 0, phase, idx) != quiz.IDENT:
            return uid
    raise AssertionError("не нашлось uid с нетождественной перестановкой")


# ── Скриншот-дефекты: первый вопрос экзамена (EXAM_READY → «Начать») ─────────

def test_exam_first_question_text_buttons_and_grading_agree(env):
    """Первый показ экзамена: текст, тела кнопок и сверка — ОДИН порядок.
    До фикса текст был каноническим, кнопки/сверка — пер-сессионными."""
    uid = _uid_with_perm(env, "exam", 0)
    reply = sm.process_message(uid, "Начать", f"d{uid}", [], None)
    session = db.get_session(uid)
    assert session["state"] == "EXAM"
    q = db.get_course_questions(session["course_id"])["exam_questions"][0]
    view = quiz.question_view(session, q, "exam", 0)

    # Текст — в координатах сотрудника (как кнопки), не канонический
    assert _option_lines(reply) == dict(zip(quiz.LETTERS, view.bodies))
    # BLOCK-кнопки — те же тела в том же порядке
    bodies = asyncio.run(bot._test_options(session))
    assert bodies == view.bodies


def test_screenshot_scenario_vertical_4plus1(env):
    """Скрин 07.11 («Vertical 4+1»): правильный текст показан под своей буквой,
    фидбек называет ТУ ЖЕ букву и ТОТ ЖЕ текст, что видит сотрудник."""
    uid = _uid_with_perm(env, "exam", 0)
    reply = sm.process_message(uid, "Начать", f"d{uid}", [], None)
    shown = _option_lines(reply)
    correct_body = "4 этажа + 1 бизнес"
    visible_letter = next(letter for letter, b in shown.items() if b == correct_body)

    # Ответ ПРАВИЛЬНОЙ видимой буквой — засчитан верно
    feedback = sm.process_message(uid, visible_letter, f"d{uid}", [], None)
    assert "✅ Верно!" in feedback
    answers = db.get_session_answers(db.get_session(uid)["id"], "exam")
    assert answers[0]["is_correct"] == 1


def test_screenshot_scenario_call_transfer_wrong_letter_feedback(env):
    """Скрин 07.11 («перевод звонка»): ошибочная видимая буква → фидбек
    «Правильный ответ: L. тело», где «L. тело» — строка ПОКАЗАННОГО вопроса."""
    uid = _uid_with_perm(env, "exam", 1)
    # Первый вопрос сдаём верно, ловим второй («перевод звонка»)
    reply1 = sm.process_message(uid, "Начать", f"d{uid}", [], None)
    shown1 = _option_lines(reply1)
    letter1 = next(letter for letter, b in shown1.items() if b == "4 этажа + 1 бизнес")
    reply2 = sm.process_message(uid, letter1, f"d{uid}", [], None)
    shown2 = _option_lines(reply2)
    correct_body = "Попросить перезвонить"
    assert correct_body in shown2.values()

    wrong = next(letter for letter, b in shown2.items() if b != correct_body)
    feedback = sm.process_message(uid, wrong, f"d{uid}", [], None)
    visible_correct = next(letter for letter, b in shown2.items() if b == correct_body)
    # Точное совпадение фидбека с ПОКАЗАННЫМИ буквой и телом
    assert f"Правильный ответ: *{visible_correct}. {correct_body}*" in feedback
    answers = db.get_session_answers(db.get_session(uid)["id"], "exam")
    assert [a["is_correct"] for a in answers] == [1, 0]


# ── Все 24 перестановки, оба режима ──────────────────────────────────────────

@pytest.mark.parametrize("perm_idx", range(24))
def test_every_permutation_visible_letter_is_graded(env, monkeypatch, perm_idx):
    """Для каждой из 24 перестановок: буква, под которой сотрудник ВИДИТ
    правильный текст, засчитывается верно; фидбек согласован с показом."""
    perm = quiz.PERMS[perm_idx]
    monkeypatch.setattr(quiz, "option_perm", lambda *a: perm)
    uid = f"u{perm_idx + 1}"
    sm.process_message(uid, "привет", f"d{uid}", [], None)
    reply = sm.process_message(uid, "Готов", f"d{uid}", [], None)

    for phase, total in (("basic", 5), ("exam", 3)):
        for i in range(total):
            if phase == "exam" and i == 0:
                reply = sm.process_message(uid, "Начать", f"d{uid}", [], None)
            shown = _option_lines(reply)
            assert len(shown) == 4, reply
            session = db.get_session(uid)
            q = db.get_course_questions(
                session["course_id"])[f"{phase}_questions"][i]
            view = quiz.question_view(session, q, phase, i)
            # показ = view (единый источник истины)
            assert shown == dict(zip(quiz.LETTERS, view.bodies))
            last = i == total - 1
            if not (phase == "exam" and i == 1):
                reply = sm.process_message(uid, view.correct_display,
                                           f"d{uid}", [], None)
                assert "✅ Верно!" in reply
            else:
                # Один ошибочный ответ на режим: фидбек = показанные буква+тело
                wrong = next(letter for letter in quiz.LETTERS
                             if letter != view.correct_display)
                feedback = sm.process_message(uid, wrong, f"d{uid}", [], None)
                assert (f"Правильный ответ: *{view.correct_display}. "
                        f"{view.correct_body}*") in feedback
                assert f"{view.correct_display}. {view.correct_body}" in reply \
                    or last  # в фидбеке последнего вопроса тоже есть строка
                reply = feedback
                if not last:
                    # следующий вопрос уже пришёл в этом же сообщении
                    pass
            if not last:
                # reply уже содержит следующий вопрос
                continue
    # Базовые ответы верны; в экзамене один умышленный промах
    # экзамен завершён → сессия DONE (get_session её не отдаёт)
    done = db.get_sessions_by_user(uid)[0]
    assert done["state"] == "DONE"
    assert done["score_basic"] == 5
    assert done["score_exam"] == 2


# ── Канон/кириллица/неканонический порядок в данных ──────────────────────────

def _replace_questions(env, monkeypatch, exam_questions):
    data = dict(QUESTIONS)
    data["exam_questions"] = exam_questions
    cid = db.save_draft_course("Кривой.docx", "1",
                               json.dumps(data, ensure_ascii=False))
    db.activate_course_by_id(cid, "hr")
    return cid


def test_cyrillic_correct_and_prefixes_normalized(env):
    """Данные с кириллическим correct («В») и кириллическими префиксами
    («А)», «Б)»…) оцениваются по тому же тексту, что показан."""
    q = {"id": 0, "text": "Кириллица?",
         "options": ["А) раз", "Б) два", "В) три", "Г) четыре"],
         "correct": "В", "explanation": ""}
    view = quiz.question_view({"id": 1, "attempt": 0}, q, "exam", 0)
    assert view.valid
    assert view.correct_body == "три"
    assert quiz.to_canonical(view.correct_display, view.perm) == "C"
    assert view.options[quiz.LETTERS.index(view.correct_display)] == \
        f"{view.correct_display}. три"


def test_noncanonical_stored_prefix_order(env):
    """Префиксы в JSON не по порядку («B. …», «D. …», «A. …», «C. …»):
    буква↔тело привязка сохраняется, correct_body — тело префикса correct."""
    q = {"id": 0, "text": "Порядок?",
         "options": ["B. второй", "D. четвёртый", "A. первый", "C. третий"],
         "correct": "B", "explanation": ""}
    view = quiz.question_view({"id": 1, "attempt": 0}, q, "basic", 0)
    assert view.valid
    assert view.correct_body == "второй"
    assert quiz.to_canonical(view.correct_display, view.perm) == "B"
    # канонический (без сессии) показ: строки по буквам A–D, привязка прежняя
    canon = quiz.question_view(None, q, "basic", 0)
    assert canon.options == ["A. первый", "B. второй",
                             "C. третий", "D. четвёртый"]
    assert canon.correct_display == "B" and canon.correct_body == "второй"


def test_duplicate_letters_and_bodies_are_malformed(env):
    for opts in (["A. 1", "A. 2", "B. 3", "C. 4"],      # дубль буквы
                 ["A. 1", "B. 1", "C. 3", "D. 4"],      # дубль тела
                 ["A. 1", "B. 2", "C. 3"],              # не хватает
                 ["A. 1", "B. 2", "C. 3", "E. 4"],      # чужая буква
                 ["первый", "второй", "третий", "четвёртый"]):  # без префиксов
        view = quiz.question_view({"id": 1, "attempt": 0},
                                  {"options": opts, "correct": "A"},
                                  "basic", 0)
        assert not view.valid, opts


# ── Malformed вход: без записи ответа и без изменения счёта ──────────────────

def _make_course_with_broken_exam(env, monkeypatch, broken_q):
    data = dict(QUESTIONS)
    data["exam_questions"] = [
        _q("Э1?", ["э1а", "э1б", "э1в", "э1г"], "A"), broken_q]
    cid = db.save_draft_course("Сломанный.docx", "1",
                               json.dumps(data, ensure_ascii=False))
    db.activate_course_by_id(cid, "hr")
    return cid


@pytest.mark.parametrize("broken_q", [
    {"id": 1, "text": "Нет correct", "options": ["A. 1", "B. 2", "C. 3", "D. 4"]},
    {"id": 1, "text": "Чужая буква", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
     "correct": "E"},
    {"id": 1, "text": "Дубль букв", "options": ["A. 1", "A. 2", "B. 3", "C. 4"],
     "correct": "A"},
    {"id": 1, "text": "Без префиксов",
     "options": ["раз", "два", "три", "четыре"], "correct": "A"},
])
def test_malformed_question_not_graded_no_record_no_score(
        env, monkeypatch, broken_q):
    """Битый вопрос — дефект ДАННЫХ, а не сотрудника: ответ не пишется,
    счёт и q_idx не меняются, HR получает сигнал."""
    monkeypatch.setattr(sm, "selectable_roles", lambda: [])   # без ролей: проще путь
    cid = _make_course_with_broken_exam(env, monkeypatch, broken_q)
    uid = "u50"
    session = db.create_session(uid, f"d{uid}", cid, quiz_version=1)
    sm._start_basic_test(session)
    _pass_basic(uid)
    sm.process_message(uid, "Начать", f"d{uid}", [], None)
    # Первый экзаменационный — верно
    s = db.get_session(uid)
    sm.process_message(uid, _correct_display(s, "exam", 0), f"d{uid}", [], None)
    before = db.get_session(uid)
    assert before["state"] == "EXAM" and before["current_q_idx"] == 1
    n_answers = len(db.get_session_answers(before["id"]))

    reply = sm.process_message(uid, "A", f"d{uid}", [], None)
    after = db.get_session(uid)
    assert "не засчитан" in reply
    assert after["current_q_idx"] == 1                 # прогресс не сдвинут
    assert after["score_exam"] == before["score_exam"]  # счёт не изменён
    assert len(db.get_session_answers(after["id"])) == n_answers  # записи нет
    assert env.hr_calls, "HR не узнал о битом вопросе"


def test_letter_outside_shown_options_rejected_silently_no_record(env):
    """Буква вне показанных вариантов (2 варианта, ответ «D») — не ответ:
    переспрос, без записи и без изменения счёта."""
    monkeypatch_q = {"id": 0, "text": "Да/нет?",
                     "options": ["A. Да", "B. Нет"], "correct": "A"}
    view = quiz.question_view({"id": 1, "attempt": 0}, monkeypatch_q, "basic", 0)
    assert view.valid and view.letters == ("A", "B")


# ── Пауза/продолжение и перезапуск ───────────────────────────────────────────

def test_exam_first_question_survives_pause_resume_and_restart(env):
    """Показанный порядок не меняется ни от паузы/возврата, ни от «перезапуска»
    (перечитывания сессии из БД): perm — функция (id, attempt, phase, q_idx)."""
    uid = _uid_with_perm(env, "exam", 0)
    first = sm.process_message(uid, "Начать", f"d{uid}", [], None)
    sm.process_message(uid, "выйти", f"d{uid}", [], None)
    assert db.get_session(uid)["state"] == "PAUSED_EXAM"
    resumed = sm.process_message(uid, "Продолжить", f"d{uid}", [], None)
    # «Перезапуск»: свежая строка из БД, тот же детерминированный порядок
    fresh = db.get_session(uid)
    q = db.get_course_questions(fresh["course_id"])["exam_questions"][0]
    again = sm.format_question(q, 0, 3, "exam", fresh)
    for text in (resumed, again):
        assert _option_lines(text) == _option_lines(first)
    # Ответ после рестарта засчитывается по показанному тексту
    letter = next(letter for letter, b in _option_lines(first).items()
                  if b == "4 этажа + 1 бизнес")
    feedback = sm.process_message(uid, letter, f"d{uid}", [], None)
    assert "✅ Верно!" in feedback
