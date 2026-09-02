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
    """Вопрос в координатах сотрудника.

    options — 4 строки «A. тело» в ЕГО порядке (для текста вопроса);
    bodies  — те же тела без букв (для BLOCK-кнопок) либо None, если
              перемешивать нечего (не 4 варианта / кривые префиксы);
    perm    — perm[i] = канонический индекс варианта, стоящего у сотрудника
              на позиции i;
    correct_display — правильная буква в ЕГО координатах;
    correct_body — тело правильного варианта (для честного фидбека).
    """
    options: list
    bodies: list | None
    perm: tuple
    correct_display: str
    correct_body: str


def shuffle_enabled() -> bool:
    """Kill-switch. Читается НА ВЫЗОВЕ: тесты FSM глушат перемешивание
    через monkeypatch.setenv, прод может выключить его без релиза."""
    return os.getenv("SHUFFLE_OPTIONS", "1") == "1"


def option_perm(session_id, attempt: int, phase: str, q_idx: int) -> tuple:
    """Детерминированная перестановка вариантов для (сессия, попытка, вопрос).

    blake2b, а не встроенный hash(): при PYTHONHASHSEED=random хэш строки
    меняется между запусками процесса — сотрудник ответил бы по одному
    порядку, а размапилось бы другим (тихая порча оценки после рестарта).

    attempt входит в ключ, потому что пересдача переиспользует ТУ ЖЕ строку
    сессии: без него сотрудник, увидевший «Правильный ответ: B» по каждому
    вопросу, сдал бы пересдачу по записанным буквам, не открывая документ.
    """
    key = f"{session_id}:{attempt}|{phase}|{q_idx}".encode()
    digest = hashlib.blake2b(key, digest_size=8).digest()
    return PERMS[int.from_bytes(digest, "big") % len(PERMS)]


def to_canonical(letter: str, perm: tuple) -> str:
    """Буква, которую нажал сотрудник → каноническая (для answers и сверки)."""
    return LETTERS[perm[LETTERS.index(letter)]]


def to_display(letter: str, perm: tuple) -> str:
    """Каноническая буква → та, что видит сотрудник."""
    return LETTERS[perm.index(LETTERS.index(letter))]


def _parse_options(q: dict) -> list | None:
    """[(буква, тело)] по каноническому порядку либо None, если формат не тот
    (2 варианта в тестовых фикстурах, кривые префиксы, дубли букв)."""
    opts = [str(o).strip() for o in (q.get("options") or [])]
    if len(opts) != 4:
        return None
    parsed = []
    for o in opts:
        head, sep, body = o.partition(".")
        if not sep:
            head, sep, body = o.partition(")")
        head = head.strip().upper()
        if not sep or head not in LETTERS:
            return None
        parsed.append((head, body.strip()))
    if len({head for head, _ in parsed}) != 4:
        return None
    return parsed


def question_view(session: dict | None, q: dict, phase: str,
                  q_idx: int) -> QuestionView:
    """ЕДИНСТВЕННЫЙ источник правды о порядке вариантов для сотрудника.

    Зовут обе точки рендера — текст вопроса (state_machine.format_question) и
    тела кнопок (bitrix_bot._test_options). Если звать только из одной, кнопка
    «B» пошлёт букву не того варианта, который сотрудник прочитал.

    session=None или выключенный шафл → канонический порядок (HR-инструменты,
    легаси-вызовы). Читаются только session["id"] и session["attempt"] —
    состояние сессии сознательно НЕ трогаем: словарь у вызывающего может быть
    устаревшим по state, но id и попытка в нём всегда верны.
    """
    parsed = _parse_options(q)
    correct = q.get("correct", "")
    if parsed is None or correct not in LETTERS:
        # Нечего перемешивать — отдаём как есть (поведение до 02.09)
        opts = [str(o).strip() for o in (q.get("options") or [])]
        body = ""
        if parsed is not None:
            body = dict(parsed).get(correct, "")
        return QuestionView(opts, None, IDENT, correct, body)

    bodies_canon = [body for _letter, body in parsed]
    if session is None or not shuffle_enabled():
        return QuestionView(
            [f"{LETTERS[i]}. {bodies_canon[i]}" for i in range(4)],
            bodies_canon, IDENT, correct,
            bodies_canon[LETTERS.index(correct)])

    perm = option_perm(session["id"], session.get("attempt") or 0,
                       phase, q_idx)
    bodies = [bodies_canon[perm[i]] for i in range(4)]
    return QuestionView(
        [f"{LETTERS[i]}. {bodies[i]}" for i in range(4)],
        bodies, perm, to_display(correct, perm),
        bodies_canon[LETTERS.index(correct)])
