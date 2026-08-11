"""
app/keyboards.py — №7: инлайн-кнопки employee-бота (за флагом BUTTONS_ENABLED).

Клавиатура — функция СОСТОЯНИЯ сессии, FSM не трогается: нажатие кнопки шлёт
generic-команду «say» (scripts/register_commands.py) с COMMAND_PARAMS = ровно
тем текстом, который FSM уже понимает («A», «Готов», «2», «Пересдать»).
Событие нажатия приходит ONIMCOMMANDADD на POST /command.
"""

_BG = "#29619b"


def _btn(text: str, payload: str = None, display: str = "LINE") -> dict:
    return {"TEXT": text, "COMMAND": "say", "COMMAND_PARAMS": payload or text,
            "DISPLAY": display, "BG_COLOR": _BG, "TEXT_COLOR": "#ffffff"}


def for_session(session: dict | None, fork: bool = False,
                role_options: list | None = None) -> list | None:
    """session — СВЕЖАЯ сессия (после process_message); fork — активна
    развилка «Пересдать/Далее» (№9); role_options — selectable_roles()."""
    if session is None:
        if fork:
            return [_btn("Пересдать"), _btn("Далее")]
        return None
    state = session["state"]
    if state == "ROLE_SELECT":
        return ([_btn(str(i)) for i in range(1, len(role_options or []) + 1)]
                or None)
    if state == "READING":
        return [_btn("Готов", display="BLOCK"), _btn("Мои курсы"), _btn("Роль")]
    if state in ("BASIC_TEST", "EXAM"):
        return [_btn(x) for x in ("A", "B", "C", "D")]
    if state == "WAITING_HR":
        return [_btn("Мои курсы")]
    return None
