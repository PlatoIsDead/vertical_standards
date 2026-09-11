"""kb_tracker: кнопки только у последнего сообщения бота в диалоге."""
import threading

import pytest

import app.kb_tracker as kt


@pytest.fixture(autouse=True)
def _clean():
    kt.clear()
    yield
    kt.clear()


def test_first_message_nothing_to_strip():
    assert kt.register("54858", "28528", 100) is None
    assert kt.latest("54858", "28528") == 100


def test_newer_message_strips_previous():
    kt.register("54858", "28528", 100)
    assert kt.register("54858", "28528", 101) == 100
    assert kt.latest("54858", "28528") == 101


def test_older_message_strips_itself():
    """Параллельные отправки завершились не по порядку: кнопки у большего id."""
    kt.register("54858", "28528", 105)
    assert kt.register("54858", "28528", 103) == 103
    assert kt.latest("54858", "28528") == 105


def test_same_id_is_noop():
    """Двойная регистрация (нажатие на актуальное сообщение) ничего не снимает."""
    kt.register("54858", "28528", 100)
    assert kt.register("54858", "28528", 100) is None
    assert kt.latest("54858", "28528") == 100


def test_bots_and_dialogs_are_isolated():
    """HR-бот и бот обучения в диалоге с тем же юзером — разные чаты."""
    kt.register("54858", "28528", 100)            # HR-бот
    assert kt.register("54849", "28528", 101) is None   # бот обучения
    assert kt.register("54858", "36523", 102) is None   # другой юзер
    assert kt.latest("54858", "28528") == 100


def test_legacy_u_prefix_is_same_dialog():
    kt.register("54858", "u28528", 100)
    assert kt.register("54858", "28528", 101) == 100


@pytest.mark.parametrize("bad", [None, "", "abc", True, False, 0, -5, {}])
def test_garbage_ids_ignored(bad):
    assert kt.register("54858", "28528", bad) is None
    assert kt.latest("54858", "28528") is None


def test_numeric_string_id_accepted():
    kt.register("54858", "28528", "100")
    assert kt.register("54858", "28528", 101) == 100


def test_strip_payload_never_carries_message():
    """Пустой MESSAGE в imbot.message.update удаляет сообщение целиком."""
    p = kt.strip_payload("54858", "5106038", "cid")
    assert "MESSAGE" not in p
    assert p == {"BOT_ID": "54858", "MESSAGE_ID": 5106038, "KEYBOARD": "N",
                 "IS_EDITED": "N", "CLIENT_ID": "cid"}
    assert "CLIENT_ID" not in kt.strip_payload("54858", 1)


def test_concurrent_registrations_converge_to_max():
    """notify_hr шлёт из потока, _send — из лупа: итог не зависит от порядка."""
    stripped = []
    ids = list(range(1, 201))

    def worker(chunk):
        for i in chunk:
            loser = kt.register("54858", "28528", i)
            if loser is not None:
                stripped.append(loser)

    threads = [threading.Thread(target=worker, args=(ids[k::4],))
               for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert kt.latest("54858", "28528") == 200
    # каждое сообщение, кроме последнего, лишилось кнопок ровно один раз
    assert sorted(stripped) == list(range(1, 200))
