"""app/quiz.py — словарь состояний и пер-сессионный порядок вариантов A–D.

Чистые функции, без БД и сети.
"""
import subprocess
import sys

import pytest

from app import quiz

Q = {"id": 0, "text": "Что делать?",
     "options": ["A. Первый", "B. Второй", "C. Третий", "D. Четвёртый"],
     "correct": "B", "explanation": ""}


@pytest.fixture(autouse=True)
def shuffle_on(monkeypatch):
    monkeypatch.setenv("SHUFFLE_OPTIONS", "1")


# ── Словарь состояний ────────────────────────────────────────────────────────

def test_phase_of():
    assert quiz.phase_of("BASIC_TEST") == "basic"
    assert quiz.phase_of("PAUSED_BASIC") == "basic"
    assert quiz.phase_of("EXAM") == "exam"
    assert quiz.phase_of("PAUSED_EXAM") == "exam"
    assert quiz.phase_of("READING") is None
    assert quiz.phase_of(None) is None


def test_state_transitions():
    for phase in ("basic", "exam"):
        paused = quiz.paused_state(phase)
        assert quiz.is_paused(paused)
        assert quiz.phase_of(paused) == phase
        active = quiz.active_state(phase)
        assert not quiz.is_paused(active)
        assert quiz.phase_of(active) == phase


def test_progress_states_cover_paused():
    """Гейт «не репойнтить строку с прогрессом» обязан знать про паузы —
    иначе ответы уедут чужому курсу при «Выбрать N»."""
    for state in quiz.PAUSED_STATES + quiz.TEST_STATES:
        assert state in quiz.PROGRESS_STATES
    assert "WAITING_HR" in quiz.PROGRESS_STATES
    assert "READING" not in quiz.PROGRESS_STATES


# ── Перестановки ─────────────────────────────────────────────────────────────

def test_perm_deterministic_in_process():
    first = quiz.option_perm(42, 0, "basic", 3)
    assert all(quiz.option_perm(42, 0, "basic", 3) == first for _ in range(200))


def test_perm_survives_process_restart():
    """blake2b, а не hash(): при PYTHONHASHSEED=random встроенный хэш строки
    меняется между запусками — ответ размапился бы чужой перестановкой."""
    code = ("from app import quiz; "
            "print(quiz.option_perm(42, 0, 'basic', 3))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env={"PYTHONHASHSEED": "random",
                                         "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(quiz.option_perm(42, 0, "basic", 3))


def test_perm_varies_by_session_attempt_phase_idx():
    base = quiz.option_perm(1, 0, "basic", 0)
    assert any(quiz.option_perm(sid, 0, "basic", 0) != base
               for sid in range(2, 12))            # разные сотрудники
    assert any(quiz.option_perm(1, 0, "basic", i) != base
               for i in range(1, 12))              # разные вопросы
    assert any(quiz.option_perm(1, a, "basic", 0) != base
               for a in range(1, 12))              # разные попытки


def test_letter_mapping_round_trip():
    for perm in quiz.PERMS:
        for letter in quiz.LETTERS:
            assert quiz.to_canonical(quiz.to_display(letter, perm), perm) == letter
            assert quiz.to_display(quiz.to_canonical(letter, perm), perm) == letter


def test_question_view_keeps_bodies_and_marks_correct():
    view = quiz.question_view({"id": 7, "attempt": 0}, Q, "basic", 0)
    assert sorted(view.bodies) == ["Второй", "Первый", "Третий", "Четвёртый"]
    # правильный вариант — тот же по смыслу, буква своя
    assert view.correct_body == "Второй"
    assert view.options[quiz.LETTERS.index(view.correct_display)] == \
        f"{view.correct_display}. Второй"
    # нажатие display-буквы правильного варианта даёт канон «B»
    assert quiz.to_canonical(view.correct_display, view.perm) == "B"


def test_question_view_identity_without_session_or_flag(monkeypatch):
    canon = ["A. Первый", "B. Второй", "C. Третий", "D. Четвёртый"]
    assert quiz.question_view(None, Q, "basic", 0).options == canon
    monkeypatch.setenv("SHUFFLE_OPTIONS", "0")
    view = quiz.question_view({"id": 7, "attempt": 3}, Q, "basic", 0)
    assert view.options == canon and view.correct_display == "B"


def test_question_view_passthrough_on_odd_format():
    """Фикстуры с 2 вариантами и кривые префиксы не должны падать."""
    two = {"options": ["A. Да", "B. Нет"], "correct": "A"}
    view = quiz.question_view({"id": 1, "attempt": 0}, two, "basic", 0)
    assert view.options == ["A. Да", "B. Нет"] and view.bodies is None
    assert view.perm == quiz.IDENT and view.correct_display == "A"

    broken = {"options": ["первый", "второй", "третий", "четвёртый"],
              "correct": "A"}
    assert quiz.question_view({"id": 1, "attempt": 0}, broken,
                              "basic", 0).bodies is None


def test_question_view_ignores_stale_state():
    """view читает только id и attempt — устаревший state в словаре сессии
    (частый случай после update_session) на порядок не влияет."""
    a = quiz.question_view({"id": 5, "attempt": 1, "state": "BASIC_TEST"},
                           Q, "basic", 2)
    b = quiz.question_view({"id": 5, "attempt": 1, "state": "PAUSED_BASIC"},
                           Q, "basic", 2)
    assert a.options == b.options
