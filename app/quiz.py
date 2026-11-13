"""
app/quiz.py — словарь состояний теста и пер-сессионный порядок вариантов A–D.

Лист-модуль: только stdlib, НИ ОДНОГО импорта из app.* — поэтому его свободно
зовут state_machine, bitrix_bot, keyboards и hr_tools. Держать это в
state_machine нельзя: словарь состояний нужен keyboards.py, а state_machine сам
импортирует keyboards (был бы цикл).

ЖЕЛЕЗНЫЙ ИНВАРИАНТ (02.09). Буквы в тексте вопроса и в COMMAND_PARAMS кнопок —
ВСЕГДА координаты конкретного сотрудника (display). Канонический порядок живёт
только в questions_json, в таблице answers и в HR-инструментах. Перевод между
системами — исключительно через to_canonical() / to_display().
"""
import hashlib
import itertools
import os
import json
import re
import math
from typing import NamedTuple

# ── Словарь состояний ────────────────────────────────────────────────────────
# Пауза — настоящее состояние (02.09): раньше «Выйти» только печатал текст, и
# сессия оставалась в BASIC_TEST/EXAM, из-за чего «Помощь» и вопросы вели себя
# как в активном тесте. Две паузы вместо одной, потому что фаза (basic/exam)
# выводится из state в четырёх местах — единый PAUSED её бы уничтожил.
TEST_STATES = ("BASIC_TEST", "EXAM")
PAUSED_STATES = ("PAUSED_BASIC", "PAUSED_EXAM")
QUIZ_STATES = TEST_STATES + PAUSED_STATES

# Строки с НЕПЕРЕНОСИМЫМ прогрессом: их нельзя репойнтить на другой курс
# (гейт в _handle_course_switch) — иначе ответы уедут чужому курсу.
PROGRESS_STATES = ("WAITING_HR", "EXAM_READY") + QUIZ_STATES

_PHASE = {"BASIC_TEST": "basic", "PAUSED_BASIC": "basic",
          "EXAM": "exam", "PAUSED_EXAM": "exam"}
_PAUSED = {"basic": "PAUSED_BASIC", "exam": "PAUSED_EXAM"}
_ACTIVE = {"basic": "BASIC_TEST", "exam": "EXAM"}


def phase_of(state: str | None) -> str | None:
    """Единственный вывод фазы из состояния: basic / exam / None."""
    return _PHASE.get(state or "")


def paused_state(phase: str) -> str:
    return _PAUSED[phase]


def active_state(phase: str) -> str:
    return _ACTIVE[phase]


def is_paused(state: str | None) -> bool:
    return state in PAUSED_STATES


def phase_label(phase: str) -> str:
    """«Экзамен» / «Тест» — для текстов, где фаза называется по-русски."""
    return "Экзамен" if phase == "exam" else "Тест"


# ── Пер-сессионный порядок вариантов ─────────────────────────────────────────
LETTERS = ("A", "B", "C", "D")
IDENT = (0, 1, 2, 3)
# 24 перестановки в фиксированном порядке itertools — полностью
# специфицированы и переживают апгрейд интерпретатора (в отличие от
# random.shuffle, чей порядок опирается на внутренности _randbelow).
PERMS = tuple(itertools.permutations(IDENT))


class QuestionView(NamedTuple):
    """Options, buttons and grading in the employee's display coordinates."""
    options: list
    bodies: list | None
    perm: tuple
    correct_display: str
    correct_body: str
    valid: bool = True
    letters: tuple = ()
    correct_canonical: str = ""


def shuffle_enabled() -> bool:
    return os.getenv("SHUFFLE_OPTIONS", "1") == "1"


def option_perm(session_id, attempt: int, phase: str, q_idx: int) -> tuple:
    # Keep the deployed seed and permutation order for existing sessions.
    key = f"{session_id}:{attempt}|{phase}|{q_idx}".encode()
    digest = hashlib.blake2b(key, digest_size=8).digest()
    return PERMS[int.from_bytes(digest, "big") % len(PERMS)]


def to_canonical(letter: str, perm: tuple) -> str:
    return LETTERS[perm[LETTERS.index(letter)]]


def to_display(letter: str, perm: tuple) -> str:
    return LETTERS[perm.index(LETTERS.index(letter))]


_OPT_RE = re.compile(r"^\s*([A-DАБВГСД])\s*[).:\-–—]\s*(\S.*?)\s*$", re.I)
_VISUAL = {"А": "A", "В": "B", "С": "C", "Д": "D"}
_RUSSIAN = dict(zip("АБВГ", LETTERS))


def _parse_question(q: dict):
    if not isinstance(q, dict) or not isinstance(q.get("text", ""), str):
        return None
    opts = q.get("options")
    if not isinstance(opts, list) or len(opts) not in (2, 4):
        return None
    matches = [_OPT_RE.fullmatch(o) if isinstance(o, str) else None for o in opts]
    if not all(matches):
        return None
    raw = [m[1].upper() for m in matches]
    # Б/Г unambiguously select the Russian alphabet, also for correct.
    mapping = _RUSSIAN if set(raw) & {"Б", "Г"} else _VISUAL
    heads = [mapping.get(h, h) for h in raw]
    correct = q.get("correct")
    if not isinstance(correct, str):
        return None
    correct = mapping.get(correct.strip().upper(), correct.strip().upper())
    letters = LETTERS[:len(opts)]
    bodies = [m[2].strip() for m in matches]
    if (set(heads) != set(letters) or len(set(heads)) != len(heads)
            or len({" ".join(b.casefold().split()) for b in bodies}) != len(bodies)
            or correct not in letters):
        return None
    by_letter = dict(zip(heads, bodies))
    return [by_letter[letter] for letter in letters], correct


def question_view(session: dict | None, q: dict, phase: str,
                  q_idx: int) -> QuestionView:
    parsed = _parse_question(q)
    if parsed is None:
        opts = q.get("options", []) if isinstance(q, dict) else []
        opts = [str(o) for o in opts] if isinstance(opts, list) else []
        return QuestionView(opts, None, IDENT, "", "", False)
    bodies_canon, correct = parsed
    letters = LETTERS[:len(bodies_canon)]
    perm = IDENT
    # Two-option legacy questions keep their historical unshuffled layout.
    if len(letters) == 4 and session is not None and shuffle_enabled():
        perm = option_perm(session["id"], session.get("attempt") or 0, phase, q_idx)
    bodies = [bodies_canon[perm[i]] for i in range(len(letters))]
    display = to_display(correct, perm)
    return QuestionView(
        [f"{letter}. {body}" for letter, body in zip(letters, bodies)],
        bodies if len(letters) == 4 else None, perm, display,
        bodies_canon[LETTERS.index(correct)], True, letters, correct)


def all_questions(course: dict) -> list[dict]:
    """One deterministic list; keep malformed questions visible to validation.

    The first occurrence of a question text wins. No synthetic duplicates are
    added to reach a fixed question count. Source arrays remain intact for HR.
    """
    seen, out = set(), []
    for key in ("basic_questions", "exam_questions"):
        for q in course.get(key) or []:
            norm = " ".join(str(q.get("text", "")).casefold().split()) if isinstance(q, dict) else ""
            if norm and norm in seen:
                continue
            if norm:
                seen.add(norm)
            out.append(q)
    return out


def unified(session: dict | None) -> bool:
    return bool(session and session.get("quiz_version", 1) == 2)


def session_questions(session: dict, course: dict) -> dict:
    """Resolve a pinned question snapshot, with the live course as fallback."""
    snapshot = session.get("quiz_questions_json")
    return json.loads(snapshot) if snapshot else course


def phase_questions(session: dict, course: dict, phase: str) -> list:
    course = session_questions(session, course)
    return all_questions(course) if unified(session) else course.get(f"{phase}_questions", [])


def pass_threshold(total: int) -> int:
    return math.ceil(total * 7 / 10)


def result(session: dict, course: dict) -> tuple[int, int, bool]:
    course = session_questions(session, course)
    if unified(session):
        total = len(all_questions(course))
    else:
        total = len(course.get("exam_questions", []))
    score = session.get("score_exam") or 0
    # Keep already awarded historical results intact.
    threshold = pass_threshold(total) if unified(session) else round(total * .7)
    return score, total, bool(total and score >= threshold)


def answer_payload(session: dict, letter: str) -> str:
    phase = phase_of(session.get("state"))
    return (f"answer:{session.get('id', 0)}:{session.get('attempt') or 0}:"
            f"{phase}:{session.get('current_q_idx', 0)}:{letter}")


def payload_letter(message: str, session: dict | None) -> str | None:
    if not session or session.get("state") not in TEST_STATES:
        return None
    parts = message.split(":")
    if len(parts) != 6 or parts[-1] not in LETTERS:
        return None
    return parts[-1] if message == answer_payload(session, parts[-1]) else None
