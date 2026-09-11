"""
app/kb_tracker.py — кнопки только у последнего сообщения бота в диалоге.

Битрикс оставляет KEYBOARD под каждым отправленным сообщением навсегда: под
старыми вопросами висели A–D, и нажатие старой кнопки размапливалось чужой
перестановкой вариантов. Реестр помнит, у какого сообщения каждого бота в
каждом диалоге кнопки сейчас, и говорит, у какого их надо снять.

Правило — «побеждает больший MESSAGE_ID»: id в портале монотонны, поэтому
параллельные отправки (create_task, notify_hr из потока) без ожидания друг
друга сходятся к одному результату. Проигравший — это сообщение, у которого
снимаем кнопки; им может оказаться и только что зарегистрированное, если оно
старше известного.

Реестр в памяти: после рестарта он пуст и деградирует к прежнему поведению
(старые кнопки остаются, пока их не нажмут — нажатие устаревшей кнопки тоже
регистрируется и снимает её). Лист: только stdlib — его импортируют и
bitrix_bot, и state_machine.
"""
import threading

_lock = threading.Lock()
_latest: dict[tuple[str, str], int] = {}


def _key(bot_id, dialog_id) -> tuple[str, str]:
    d = str(dialog_id)
    if d.startswith("u") and d[1:].isdigit():   # легаси «uNNN» = тот же диалог
        d = d[1:]
    return str(bot_id), d


def parse_message_id(value) -> int | None:
    """result imbot.message.add → int id; всё прочее (True, None, мусор) → None."""
    if isinstance(value, bool):
        return None
    try:
        mid = int(value)
    except (TypeError, ValueError):
        return None
    return mid if mid > 0 else None


def register(bot_id, dialog_id, message_id) -> int | None:
    """Сообщение с кнопками появилось (или его кнопку нажали).

    Возвращает id сообщения, у которого кнопки надо снять, либо None."""
    mid = parse_message_id(message_id)
    if mid is None or not dialog_id:
        return None
    key = _key(bot_id, dialog_id)
    with _lock:
        prev = _latest.get(key)
        if prev is None or mid > prev:
            _latest[key] = mid
            return prev
        if mid == prev:
            return None
        return mid          # пришло более старое сообщение — снимаем его


def latest(bot_id, dialog_id) -> int | None:
    with _lock:
        return _latest.get(_key(bot_id, dialog_id))


def clear() -> None:
    with _lock:
        _latest.clear()


def strip_payload(bot_id, message_id, client_id: str = "") -> dict:
    """Тело imbot.message.update, снимающее кнопки.

    Ключа MESSAGE здесь нет НАМЕРЕННО: по докам Битрикса пустой MESSAGE
    удаляет сообщение целиком, а отсутствующий — сохраняет текст (проверено
    на портале 11.09). IS_EDITED=N — без пометки «изменено»."""
    payload = {"BOT_ID": str(bot_id), "MESSAGE_ID": int(message_id),
               "KEYBOARD": "N", "IS_EDITED": "N"}
    if client_id:
        payload["CLIENT_ID"] = client_id
    return payload
