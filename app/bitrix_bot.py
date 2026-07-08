import asyncio
import json
import os
import re
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request

import app.index_store as index_store
from app import gamification
from app.db import (
    activate_course_by_id,
    add_employee,
    count_processed_by_doc_name,
    get_all_employees,
    get_course_by_doc_name,
    get_course_by_id,
    get_course_questions,
    get_employee_by_email,
    get_pending_courses,
    get_processed_by_folders,
    get_processed_file,
    get_report_rows,
    get_session,
    get_session_answers,
    get_session_dialog_id,
    get_sessions_by_user,
    get_user_departments,
    init_db,
    is_employee_allowed,
    is_file_processed,
    is_user_seen,
    mark_file_processed,
    mark_user_seen,
    remove_processed_file,
    save_draft_course,
    seen_users_empty,
    set_course_archived,
    update_course_questions,
    update_session_by_user,
    update_user_departments,
)
from app.doc_parsers import SUPPORTED_EXTS, parse_file
from app.hr_tools import (
    REPLACEMENT_TEMPLATE,
    apply_replacement,
    build_history_text,
    build_report_text,
    format_question_full,
    parse_replacement,
    question_by_ref,
)
from app.rag import load_index
from app.roles import load_roles_config
from app.state_machine import process_message, start_onboarding

load_dotenv()

app = FastAPI()

BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL", "")
BOT_ID = os.getenv("BOT_ID", "54849")          # Employee bot
HR_BOT_ID = os.getenv("HR_BOT_ID", BOT_ID)    # HR bot (falls back to employee bot if not set)
# Bot CLIENT_ID (application_token) for PROACTIVE sends (HR notify, autostart) where
# there's no inbound webhook to read auth[application_token] from. Without it Bitrix → 403.
BOT_CLIENT_ID = os.getenv("BOT_CLIENT_ID", "")
HR_CLIENT_ID = os.getenv("HR_CLIENT_ID", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "300"))  # default 5 min
USER_POLL_INTERVAL = int(os.getenv("USER_POLL_INTERVAL_SEC", "600"))  # опрос отделов
RATING_CHECK_INTERVAL = 3600  # ежечасная проверка «пора ли постить рейтинг» (№5)

# Initialise DB and load RAG index at startup
init_db()
chunks, embeddings = load_index()


_poller_task = None
_user_poller_task = None
_rating_task = None
_processing: set[str] = set()  # file IDs currently being processed

# Dedup of inbound messages: retried sends delay replies, so users resend the same
# message and the FSM would advance per message. Skip identical (user, text) within window.
DEDUP_WINDOW_SEC = 15
_recent_msgs: dict[tuple[str, str], float] = {}

# Двухшаговая правка вопроса (№3): hr_user_id → {course_id, q_num, expires}.
# In-memory: рестарт теряет незавершённую правку — HR просто повторит команду.
PENDING_EDIT_TTL = 600
_pending_edits: dict[str, dict] = {}

# Начало любого из этих слов = команда; при живом pending команда важнее правки
_HR_COMMAND_PREFIXES = ("курсы", "курс", "список", "подтвердить", "допустить",
                        "пригласить", "вопросы", "изменить", "история",
                        "отчёт", "отчет")


def _get_pending(user_id: str) -> dict | None:
    pending = _pending_edits.get(user_id)
    if pending and pending["expires"] < time.monotonic():
        _pending_edits.pop(user_id, None)
        return None
    return pending


def _is_duplicate(user_id: str, dialog_id: str, question: str) -> bool:
    """True if this (user, text) arrived within DEDUP_WINDOW_SEC; also prunes stale keys.

    Tradeoff: answering the SAME letter to two different questions within 15s gets
    dropped. For the real exam rhythm (read question → answer) that's near-impossible.
    """
    now = time.monotonic()
    for k, ts in list(_recent_msgs.items()):
        if now - ts > DEDUP_WINDOW_SEC:
            del _recent_msgs[k]
    key = (user_id or dialog_id, question)
    if key in _recent_msgs and now - _recent_msgs[key] < DEDUP_WINDOW_SEC:
        return True
    _recent_msgs[key] = now
    return False


@app.on_event("startup")
async def start_disk_poller():
    global _poller_task, _user_poller_task, _rating_task
    _poller_task = asyncio.create_task(_disk_poll_loop())
    _user_poller_task = asyncio.create_task(_user_poll_loop())
    _rating_task = asyncio.create_task(_weekly_rating_loop())


async def _weekly_rating_loop():
    """Еженедельный рейтинг в Ленту (№5): ежечасная проверка + идемпотентный
    гейт через meta — рестарты пост не задваивают, пропущенный пн догоняется."""
    print("[rating] Started — weekly, Monday 10:00 MSK (check hourly)")
    while True:
        await asyncio.sleep(RATING_CHECK_INTERVAL)
        try:
            posted = await asyncio.to_thread(gamification.maybe_post_weekly_rating)
            if posted:
                print("[rating] weekly rating posted")
        except Exception as exc:
            print(f"[rating] ERROR: {exc!r}")


def _monitored_folders() -> dict[str, list[str]]:
    """folder_id → роли документов из этой папки. Ролевые папки из data/roles.json
    + легаси-папка MONITOR_FOLDER_ID (роль all_staff). Читается каждый цикл —
    конфиг можно править без рестарта."""
    folders = {
        str(fid): list(roles)
        for fid, roles in load_roles_config().get("folders", {}).items()
    }
    legacy = os.getenv("MONITOR_FOLDER_ID")
    if legacy and legacy not in folders:
        folders[legacy] = ["all_staff"]
    return folders


async def _disk_poll_loop():
    """Poll Bitrix Disk role folders every POLL_INTERVAL seconds for new files."""
    folders = _monitored_folders()
    if not folders:
        print("[poller] No folders configured (roles.json/MONITOR_FOLDER_ID) — waiting")
    print(f"[poller] Started — folders={list(folders)}, interval={POLL_INTERVAL}s")
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        for folder_id, roles in _monitored_folders().items():
            await _sync_folder(folder_id, roles)


async def _process_and_release(file_id: str, file_name: str,
                               roles: list[str], folder_id: str):
    try:
        await process_new_document(file_id, file_name, roles, folder_id)
    finally:
        _processing.discard(file_id)


MAX_FOLDER_DEPTH = 5  # рекурсия по подпапкам ролевой папки (№4)

_missing_strikes: dict[str, int] = {}  # file_id → подряд-промахи листинга (two-strike)
_logged_file_keys = False  # разовый лог ключей файлового объекта (проверка UPDATE_TIME)


async def _list_children(client: httpx.AsyncClient, folder_id: str,
                         type_: str) -> list[dict]:
    r = await client.post(
        BITRIX_WEBHOOK_URL + "disk.folder.getchildren",
        json={"id": folder_id, "filter": {"TYPE": type_}},
    )
    r.raise_for_status()
    return r.json().get("result", [])


async def _walk_folder(client, folder_id: str, roles: list[str], depth: int,
                       seen_files: dict, visited_folders: set):
    """Рекурсивный обход папки: файлы в обработку, подпапки — с ролями корня.
    Подпапка, сама замапленная в roles.json, пропускается (её обходит главный
    цикл со своими ролями)."""
    global _logged_file_keys
    visited_folders.add(str(folder_id))
    files = await _list_children(client, folder_id, "file")
    if files and not _logged_file_keys:
        _logged_file_keys = True
        print(f"[poller] file object keys: {sorted(files[0].keys())}")

    for f in files:
        file_id = str(f.get("ID", ""))
        file_name = f.get("NAME", "")
        if not file_id:
            continue
        seen_files[file_id] = f
        ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
        if ext not in SUPPORTED_EXTS:
            continue
        if file_id in _processing:
            continue
        # processed_files сидируется из courses.doc_id в init_db, поэтому
        # покрывает и файлы, обработанные до появления этой таблицы.
        row = await asyncio.to_thread(get_processed_file, file_id)
        if row:
            new_time = str(f.get("UPDATE_TIME") or "")
            old_time = str(row.get("update_time") or "")
            if not (new_time and old_time and new_time != old_time):
                continue  # обработан и не менялся
            # Перезапись через веб-UI Bitrix = новая ВЕРСИЯ с тем же file_id:
            # гейт по file_id слеп, детектим по UPDATE_TIME → реингест как замена.
            print(f"[poller] Version change: {file_name} (id={file_id}, "
                  f"{old_time!r} → {new_time!r})")
            await asyncio.to_thread(remove_processed_file, file_id)
        else:
            print(f"[poller] New file detected: {file_name} (id={file_id}, roles={roles})")
        _processing.add(file_id)
        asyncio.create_task(
            _process_and_release(file_id, file_name, roles, str(folder_id))
        )

    if depth > 1:
        monitored = _monitored_folders()
        for sub in await _list_children(client, folder_id, "folder"):
            sub_id = str(sub.get("ID", ""))
            if not sub_id or sub_id in monitored:
                continue
            await _walk_folder(client, sub_id, roles, depth - 1,
                               seen_files, visited_folders)


async def _sync_folder(root_id: str, roles: list[str]):
    """Обход корневой ролевой папки + детект удалённых файлов (two-strike:
    удаляем только со второго подряд промаха — транзиентный сбой не удаляет)."""
    seen_files: dict[str, dict] = {}
    visited: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _walk_folder(client, root_id, roles, MAX_FOLDER_DEPTH,
                               seen_files, visited)
    except Exception as exc:
        import traceback
        print(f"[poller] ERROR folder={root_id}: {exc}\n{traceback.format_exc()}")
        return  # листинг не удался — промахи НЕ засчитываем

    processed = await asyncio.to_thread(get_processed_by_folders, sorted(visited))
    for row in processed:
        fid = str(row["file_id"])
        if fid in seen_files:
            _missing_strikes.pop(fid, None)
            continue
        if fid in _processing:
            continue
        _missing_strikes[fid] = _missing_strikes.get(fid, 0) + 1
        if _missing_strikes[fid] < 2:
            print(f"[sync] {row['doc_name']!r} (id={fid}) missing — strike 1/2")
            continue
        await _delete_document(row)


async def _delete_document(row: dict) -> None:
    """Файл удалён из папки: чанки из индекса, строку из processed_files;
    курс архивируется, только если копий документа в других папках не осталось."""
    global chunks, embeddings
    doc_name, folder_id, file_id = row["doc_name"], row["folder_id"], str(row["file_id"])
    removed = await asyncio.to_thread(index_store.remove_document, doc_name, folder_id)
    await asyncio.to_thread(remove_processed_file, file_id)
    _missing_strikes.pop(file_id, None)
    archived = False
    remaining = await asyncio.to_thread(count_processed_by_doc_name, doc_name)
    if remaining == 0:
        course = await asyncio.to_thread(get_course_by_doc_name, doc_name)
        if course and not course.get("archived_at"):
            await asyncio.to_thread(set_course_archived, course["id"], True)
            archived = True
    chunks, embeddings = load_index()
    print(f"[sync] deleted {doc_name!r} (folder {folder_id}): -{removed} chunks"
          + (", course archived" if archived else ""))
    text = (f"🗑 Документ «{doc_name}» удалён из папки — "
            f"{removed} фрагментов убрано из поиска.")
    if archived:
        text += "\nКурс архивирован — новым сотрудникам он не назначается."
    for hr_id in _hr_ids():
        await _send(str(hr_id), text, HR_BOT_ID)


def _hr_ids() -> list[str]:
    return [x.strip() for x in os.getenv("HR_USER_IDS", "").split(",") if x.strip()]


def _extract_email(text: str) -> str | None:
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    return m.group(0).lower() if m else None


async def _apply_pending_edit(user_id: str, pending: dict, raw: str) -> str:
    """Шаг 2 правки: разобрать замену, применить к questions_json курса."""
    repl = parse_replacement(raw)
    if repl is None:
        return ("❌ Не понял формат. Пришли вопрос одним сообщением:\n\n"
                + REPLACEMENT_TEMPLATE + "\n\nИли напиши: Отмена")
    questions = await asyncio.to_thread(get_course_questions, pending["course_id"])
    try:
        questions = apply_replacement(questions, pending["q_num"], repl)
    except ValueError as exc:
        return f"❌ {exc}"
    await asyncio.to_thread(
        update_course_questions, pending["course_id"],
        json.dumps(questions, ensure_ascii=False),
    )
    _pending_edits.pop(user_id, None)
    course = await asyncio.to_thread(get_course_by_id, pending["course_id"])
    new_q = question_by_ref(questions, pending["q_num"])
    doc_name = course["doc_name"] if course else f"№{pending['course_id']}"
    return (f"✅ Вопрос {pending['q_num']} курса «{doc_name}» обновлён.\n\n"
            + format_question_full(new_q, pending["q_num"]))


async def _bitrix_user_by_email(email: str) -> dict | None:
    """user.get по email. Фильтр плоскими полями — как в официальном примере доков
    (data/referance/bitrix24_docs.md, «Примеры кода» user.get); если живьём фильтр
    игнорируется — переключить на вложенную форму {"FILTER": {"EMAIL": ...}}.
    Требует у вебхука скоуп user/user_basic (user_brief режет контакты)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            BITRIX_WEBHOOK_URL + "user.get",
            json={"EMAIL": email, "ACTIVE": True},
        )
        r.raise_for_status()
        result = r.json().get("result", [])
    # Точное совпадение на нашей стороне — страховка от проигнорированного фильтра
    matches = [u for u in result if (u.get("EMAIL") or "").strip().lower() == email]
    return matches[0] if matches else None


# ── Поллер отделов: новые сотрудники и переводы → уведомление HR ─────────────

def _watched_departments() -> list[str]:
    return [x.strip() for x in os.getenv("WATCH_DEPARTMENT_IDS", "").split(",") if x.strip()]


async def _user_poll_loop():
    """Опрос отделов из WATCH_DEPARTMENT_IDS раз в USER_POLL_INTERVAL секунд."""
    if not _watched_departments():
        print("[user-poller] disabled (WATCH_DEPARTMENT_IDS empty)")
        return
    print(f"[user-poller] Started — departments={_watched_departments()}, "
          f"interval={USER_POLL_INTERVAL}s")
    while True:
        await asyncio.sleep(USER_POLL_INTERVAL)
        # Первый прогон — тихий засев, иначе HR получит весь отдел одним залпом
        silent = await asyncio.to_thread(seen_users_empty)
        for dep_id in _watched_departments():
            await _check_department(dep_id, silent)


async def _department_users(client: httpx.AsyncClient, dep_id: str) -> list[dict]:
    """user.get по отделу с пагинацией: страница жёстко 50, продолжение через next."""
    users, start = [], 0
    while True:
        r = await client.post(
            BITRIX_WEBHOOK_URL + "user.get",
            json={"UF_DEPARTMENT": int(dep_id), "ACTIVE": True, "start": start},
        )
        r.raise_for_status()
        data = r.json()
        users += data.get("result", [])
        if data.get("next") is None:
            return users
        start = data["next"]


def _departments_json(user: dict) -> str:
    return json.dumps(sorted(int(d) for d in (user.get("UF_DEPARTMENT") or [])))


async def _check_department(dep_id: str, silent: bool):
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            users = await _department_users(client, dep_id)
        for u in users:
            uid = str(u.get("ID", ""))
            if not uid:
                continue
            deps_json = _departments_json(u)
            if not await asyncio.to_thread(is_user_seen, uid):
                await asyncio.to_thread(mark_user_seen, uid, deps_json)
                if not silent and not await asyncio.to_thread(is_employee_allowed, uid):
                    await _notify_hr_about_user(u, transfer=False)
            elif await asyncio.to_thread(get_user_departments, uid) != deps_json:
                await asyncio.to_thread(update_user_departments, uid, deps_json)
                if not silent:
                    await _notify_hr_about_user(u, transfer=True)
    except Exception as exc:
        import traceback
        print(f"[user-poller] ERROR dep={dep_id}: {exc}\n{traceback.format_exc()}")


async def _notify_hr_about_user(user: dict, transfer: bool):
    fio = (f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
           or f"ID {user.get('ID')}")
    email = (user.get("EMAIL") or "").strip().lower()
    if transfer:
        text = f"🔄 {fio} сменил отдел. Если поменялась роль — пусть напишет боту «Роль»."
    else:
        text = f"👤 Новый сотрудник: {fio} ({email or 'email не указан'})."
        if email:
            text += f"\nПригласить в обучение: Пригласить {email}"
    for hr_id in _hr_ids():
        await _send(str(hr_id), text, HR_BOT_ID)


async def _send(dialog_id: str, text: str, bot_id: str = None, client_id: str = "") -> None:
    # Network to portal.becar.ru flaps (ConnectTimeout then 200 within seconds),
    # so retry with backoff to land in a live window instead of dropping the reply.
    bot_id = bot_id or BOT_ID
    # Proactive sends pass no client_id → fall back to the bot's token from .env,
    # otherwise Bitrix rejects imbot.message.add with 403.
    if not client_id:
        client_id = HR_CLIENT_ID if str(bot_id) == str(HR_BOT_ID) else BOT_CLIENT_ID
    payload = {
        "BOT_ID": bot_id,
        "DIALOG_ID": dialog_id,
        "MESSAGE": text,
        "CLIENT_ID": client_id,
    }
    last_exc = None
    for attempt in range(1, 6):  # up to 5 attempts: backoff 2,4,6,8s
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    BITRIX_WEBHOOK_URL + "imbot.message.add", json=payload
                )
            print(f"BITRIX SEND: {resp.status_code} → {dialog_id} (attempt {attempt})")
            return
        except Exception as exc:
            last_exc = exc
            print(f"BITRIX SEND retry {attempt}/5 (dialog={dialog_id}): {exc!r}")
            if attempt < 5:
                await asyncio.sleep(2 * attempt)
    print(f"BITRIX SEND ERROR after 5 attempts (dialog={dialog_id}): {last_exc!r}")


@app.post("/")
async def bot_handler(request: Request):
    form = await request.form()

    question = (form.get("data[PARAMS][MESSAGE]") or "").strip()
    dialog_id = form.get("data[PARAMS][DIALOG_ID]")
    user_id = (
        form.get("data[PARAMS][FROM_USER_ID]") or form.get("data[USER][ID]") or ""
    ).strip()
    bot_id = next(
        (k.split("][")[1] for k in form if k.startswith("data[BOT][")), BOT_ID
    )
    client_id = form.get("auth[application_token]", "")

    print(f"MSG user={user_id!r} dialog={dialog_id!r} q={question!r}")

    if not question or not dialog_id:
        return {"status": "ok"}

    if _is_duplicate(user_id, dialog_id, question):
        print(f"DEDUP skip (dialog={dialog_id}): {question!r}")
        return {"status": "ok"}

    # ── Employee onboarding / RAG ─────────────────────────────────────────────
    had_session = bool(await asyncio.to_thread(get_session, user_id))
    try:
        text = await asyncio.to_thread(
            process_message, user_id, question, dialog_id, chunks, embeddings
        )
    except Exception as exc:
        import traceback
        print(f"process_message ERROR: {exc}\n{traceback.format_exc()}")
        text = (
            "⚠️ Сервис ИИ временно недоступен из-за проблем со связью. "
            "Попробуй, пожалуйста, ещё раз через минуту."
        )

    # Новая сессия создана этим сообщением → приложить файл документа (№4)
    if not had_session:
        new_session = await asyncio.to_thread(get_session, user_id)
        if new_session:
            asyncio.create_task(
                _send_course_file(dialog_id, new_session["course_id"])
            )

    asyncio.create_task(_send(dialog_id, text, bot_id, client_id))
    return {"status": "ok"}


@app.post("/hr")
async def hr_handler(request: Request):
    form = await request.form()

    question = (form.get("data[PARAMS][MESSAGE]") or "").strip()
    dialog_id = form.get("data[PARAMS][DIALOG_ID]")
    user_id = (
        form.get("data[PARAMS][FROM_USER_ID]") or form.get("data[USER][ID]") or ""
    ).strip()
    client_id = form.get("auth[application_token]", "")

    print(f"HR MSG user={user_id!r} dialog={dialog_id!r} q={question!r}")

    if not question or not dialog_id:
        return {"status": "ok"}

    if _is_duplicate(user_id, dialog_id, question):
        print(f"DEDUP skip HR (dialog={dialog_id}): {question!r}")
        return {"status": "ok"}

    # Гейт: HR-боту командуют только HR (пустой HR_USER_IDS = гейт выключен, dev)
    hr_ids = _hr_ids()
    if hr_ids and user_id not in hr_ids:
        print(f"HR GATE reject user={user_id!r}")
        asyncio.create_task(_send(
            dialog_id, "⛔ Команды HR-бота доступны только HR-менеджерам.",
            HR_BOT_ID, client_id,
        ))
        return {"status": "ok"}

    msg_lower = question.lower()

    # ── Двухшаговая правка вопроса: живой pending перехватывает сообщение ────
    pending = _get_pending(user_id)
    if pending:
        if msg_lower in ("отмена", "cancel"):
            _pending_edits.pop(user_id, None)
            asyncio.create_task(_send(dialog_id, "Правка отменена.",
                                      HR_BOT_ID, client_id))
            return {"status": "ok"}
        if not msg_lower.startswith(_HR_COMMAND_PREFIXES):
            text = await _apply_pending_edit(user_id, pending, question)
            asyncio.create_task(_send(dialog_id, text, HR_BOT_ID, client_id))
            return {"status": "ok"}
        _pending_edits.pop(user_id, None)  # команда важнее забытой правки

    if msg_lower in ("курсы", "курс", "список"):
        pending = await asyncio.to_thread(get_pending_courses)
        if not pending:
            text = "✅ Нет курсов, ожидающих активации."
        else:
            lines = ["📋 *Курсы на проверке:*\n"]
            for c in pending:
                name = c['doc_name']
                n = c['id']
                lines.append(f"*{name}*")
                lines.append(f"  👁 Вопросы {n}   ✅ Подтвердить {n}\n")
            text = "\n".join(lines)

    elif msg_lower.startswith("подтвердить"):
        parts = question.split()
        if len(parts) < 2:
            text = "❌ Укажи номер курса: Подтвердить {N}"
        else:
            try:
                course_id = int(parts[-1])
                course = await asyncio.to_thread(get_course_by_id, course_id)
                if not course:
                    text = f"❌ Курс №{course_id} не найден."
                else:
                    ok = await asyncio.to_thread(activate_course_by_id, course_id, user_id)
                    text = (
                        f"✅ Курс «{course['doc_name']}» активирован. Сотрудники могут начать обучение."
                        if ok else f"❌ Не удалось активировать курс №{course_id}."
                    )
            except ValueError:
                text = "❌ Укажи числовой номер курса: Подтвердить {N}"

    elif msg_lower.startswith("пригласить"):
        email = _extract_email(question)
        if not email:
            text = "❌ Укажи email сотрудника: Пригласить ivanov@company.ru"
        else:
            try:
                user = await _bitrix_user_by_email(email)
            except httpx.HTTPError as exc:
                print(f"[invite] user.get ERROR: {exc!r}")
                text = "⚠️ Битрикс не ответил. Попробуй ещё раз через минуту."
            else:
                if user is None:
                    text = f"⚠️ Сотрудник с email {email} не найден в Битриксе."
                else:
                    uid = str(user.get("ID"))
                    fio = (f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
                           or email)
                    await asyncio.to_thread(add_employee, uid, email, fio, user_id)
                    if await asyncio.to_thread(get_session, uid):
                        text = f"ℹ️ {fio} уже проходит обучение."
                    else:
                        first_msg = await asyncio.to_thread(
                            start_onboarding, uid, f"u{uid}"
                        )
                        if first_msg is None:
                            text = (
                                f"✅ {fio} добавлен в доступ. Активных курсов нет — "
                                f"после активации курса отправь: Пригласить {email}"
                            )
                        else:
                            # Автостарт идёт от EMPLOYEE-бота: client_id формы
                            # принадлежит HR-боту — _send возьмёт BOT_CLIENT_ID из .env
                            asyncio.create_task(_send(f"u{uid}", first_msg, BOT_ID))
                            new_session = await asyncio.to_thread(get_session, uid)
                            if new_session:
                                asyncio.create_task(_send_course_file(
                                    f"u{uid}", new_session["course_id"]))
                            text = (f"✅ {fio} ({email}) приглашён — "
                                    "отправил ему первое сообщение.")

    elif msg_lower.startswith("допустить"):
        parts = question.split()
        target_uid = parts[-1] if len(parts) >= 2 else None
        if target_uid and "@" in target_uid:
            emp = await asyncio.to_thread(get_employee_by_email, target_uid)
            if emp is None:
                text = (f"⚠️ {target_uid} не найден среди приглашённых. "
                        f"Сначала: Пригласить {target_uid}")
                target_uid = None
            else:
                target_uid = emp["bitrix_uid"]
        if len(parts) < 2:
            text = "❌ Укажи ID или email сотрудника: Допустить {user_id или email}"
        elif target_uid:
            found = await asyncio.to_thread(update_session_by_user, target_uid, "EXAM", 0)
            if found:
                emp_dialog = await asyncio.to_thread(get_session_dialog_id, target_uid)
                if emp_dialog:
                    await _send(
                        emp_dialog,
                        "🎓 HR допустил тебя к экзамену! Напиши что-нибудь, чтобы начать.",
                        BOT_ID, client_id,
                    )
                text = f"✅ Сотрудник {target_uid} допущен к экзамену."
            else:
                text = f"❌ Активная сессия для сотрудника {target_uid} не найдена."

    elif msg_lower.startswith("вопросы"):
        parts = question.split()
        if len(parts) < 2:
            text = "❌ Укажи номер курса: Вопросы {N}"
        else:
            try:
                course_id = int(parts[-1])
                course = await asyncio.to_thread(get_course_by_id, course_id)
                if not course:
                    text = f"❌ Курс №{course_id} не найден."
                else:
                    questions = await asyncio.to_thread(get_course_questions, course_id)
                    basic = questions.get("basic_questions", [])
                    exam = questions.get("exam_questions", [])
                    # Сквозная нумерация 1–15: 1–5 базовые, 6–15 экзамен
                    # (та же, что в команде «Изменить»)
                    lines = [f"📋 *{course['doc_name']}*\n"]
                    lines.append("*Базовые вопросы (1–5):*")
                    for i, q in enumerate(basic, 1):
                        lines.append(f"{i}. {q['text']} → {q['correct']}")
                    lines.append("\n*Экзаменационные вопросы (6–15):*")
                    for i, q in enumerate(exam, 6):
                        lines.append(f"{i}. {q['text']} → {q['correct']}")
                    lines.append(f"\nДля активации: Подтвердить {course_id}")
                    lines.append(f"Изменить вопрос: Изменить {course_id} {{номер 1–15}}")
                    text = "\n".join(lines)
            except ValueError:
                text = "❌ Укажи числовой номер курса: Вопросы {N}"

    elif msg_lower.startswith("изменить"):
        parts = question.split()
        course_id = q_num = None
        if len(parts) >= 3:
            try:
                course_id, q_num = int(parts[1]), int(parts[2])
            except ValueError:
                pass
        if course_id is None:
            text = "❌ Формат: Изменить {номер курса} {номер вопроса 1–15}"
        else:
            course = await asyncio.to_thread(get_course_by_id, course_id)
            if not course:
                text = f"❌ Курс №{course_id} не найден."
            else:
                questions = await asyncio.to_thread(get_course_questions, course_id)
                q = question_by_ref(questions, q_num)
                if q is None:
                    text = ("❌ Номер вопроса — от 1 (первый базовый) "
                            "до 15 (последний экзаменационный).")
                else:
                    _pending_edits[user_id] = {
                        "course_id": course_id, "q_num": q_num,
                        "expires": time.monotonic() + PENDING_EDIT_TTL,
                    }
                    text = (
                        f"✏️ Курс «{course['doc_name']}», сейчас:\n\n"
                        + format_question_full(q, q_num)
                        + "\n\nПришли новый вопрос одним сообщением:\n\n"
                        + REPLACEMENT_TEMPLATE
                        + "\n\n(Пояснение необязательно; без него старое "
                          "пояснение удаляется.)\nОтмена — выйти без изменений."
                    )

    elif msg_lower.startswith("история"):
        parts = question.split()
        if len(parts) < 2:
            text = "❌ Укажи email или ID: История ivanov@company.ru"
        else:
            target = parts[-1]
            uid, label = None, target
            if "@" in target:
                emp = await asyncio.to_thread(get_employee_by_email, target)
                if emp is None:
                    text = (f"⚠️ {target.lower()} не найден среди приглашённых. "
                            f"Сначала: Пригласить {target.lower()}")
                else:
                    uid = emp["bitrix_uid"]
                    label = f"{emp.get('full_name') or uid} ({emp['email']})"
            else:
                uid = target
            if uid:
                sessions = await asyncio.to_thread(get_sessions_by_user, uid)
                if not sessions:
                    text = f"У сотрудника {label} нет сессий обучения."
                else:
                    answers_by_session: dict[int, list] = {}
                    questions_by_course: dict[int, dict] = {}
                    course_names: dict[int, str] = {}
                    for s in sessions[:3]:
                        answers_by_session[s["id"]] = await asyncio.to_thread(
                            get_session_answers, s["id"])
                        cid = s["course_id"]
                        if cid not in questions_by_course:
                            questions_by_course[cid] = await asyncio.to_thread(
                                get_course_questions, cid)
                            c = await asyncio.to_thread(get_course_by_id, cid)
                            course_names[cid] = c["doc_name"] if c else f"курс №{cid}"
                    text = build_history_text(label, sessions, answers_by_session,
                                              questions_by_course, course_names)

    elif msg_lower in ("отчёт", "отчет"):
        rows = await asyncio.to_thread(get_report_rows)
        employees = {e["bitrix_uid"]: e
                     for e in await asyncio.to_thread(get_all_employees)}
        text = build_report_text(rows, employees)

    else:
        text = (
            "Доступные команды:\n"
            "• *Курсы* — список курсов на проверке\n"
            "• *Вопросы {N}* — посмотреть вопросы курса\n"
            "• *Подтвердить {N}* — активировать курс\n"
            "• *Изменить {курс} {вопрос 1–15}* — править вопрос курса\n"
            "• *Пригласить {email}* — дать доступ и начать обучение\n"
            "• *Допустить {email или ID}* — допустить сотрудника к экзамену\n"
            "• *История {email или ID}* — вопросы и ответы сотрудника\n"
            "• *Отчёт* — сводка: кто прошёл обучение и с каким результатом"
        )

    asyncio.create_task(_send(dialog_id, text, HR_BOT_ID, client_id))
    return {"status": "ok"}


@app.post("/disk-webhook")
async def disk_webhook(request: Request):
    form = await request.form()
    event = form.get("event", "")

    # Log all fields to help debug payload structure in production
    print(f"[disk-webhook] event={event!r} fields={dict(form)}")

    if event != "OnDiskFileAdd":
        return {"status": "ignored"}

    file_id = form.get("data[FIELDS_AFTER][ID]")
    file_name = form.get("data[FIELDS_AFTER][NAME]") or ""
    folder_id = form.get("data[FIELDS_AFTER][PARENT_ID]")

    if not file_id:
        return {"status": "no_file_id"}

    folders = _monitored_folders()
    roles = folders.get(str(folder_id))
    if folders and roles is None:
        print(f"[disk-webhook] Skipping folder {folder_id!r} (watching {list(folders)})")
        return {"status": "wrong_folder"}

    # Background task — respond to Bitrix immediately
    asyncio.create_task(
        process_new_document(file_id, file_name, roles or ["all_staff"], folder_id)
    )
    return {"status": "ok"}


@app.post("/user-webhook")
async def user_webhook(request: Request):
    """OnUserAdd (если событие доступно исходящему вебхуку портала).
    Только уведомляет HR — автостарт исключительно через «Пригласить»."""
    form = await request.form()
    # Имена событий приходят UPPERCASE ('ONCRMDEALADD' в примере доков)
    event = (form.get("event") or "").upper()
    if event != "ONUSERADD":
        return {"status": "ignored"}

    print(f"[user-webhook] fields={dict(form)}")  # формат data неизвестен — логируем всё
    uid = (form.get("data[ID]") or form.get("data[FIELDS][ID]")
           or form.get("data[FIELDS_AFTER][ID]"))
    if not uid:
        return {"status": "no_user_id"}
    uid = str(uid)

    if await asyncio.to_thread(is_user_seen, uid):
        return {"status": "already_seen"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(BITRIX_WEBHOOK_URL + "user.get", json={"ID": uid})
            r.raise_for_status()
            result = r.json().get("result", [])
        user = result[0] if result else {"ID": uid}
        await asyncio.to_thread(mark_user_seen, uid, _departments_json(user))
        if not await asyncio.to_thread(is_employee_allowed, uid):
            await _notify_hr_about_user(user, transfer=False)
    except Exception as exc:
        import traceback
        print(f"[user-webhook] ERROR uid={uid}: {exc}\n{traceback.format_exc()}")
    return {"status": "ok"}


async def process_new_document(file_id: str, file_name: str,
                               roles: list[str] = None,
                               folder_id: str = None) -> None:
    """Download from Bitrix Disk, update RAG index, generate questions, notify HR.

    roles — роли-адресаты чанков этого документа (из ролевой папки)."""
    global chunks, embeddings

    roles = roles or ["all_staff"]
    print(f"[process_new_document] START file_id={file_id!r} name={file_name!r} roles={roles}")

    # Защита от двойного срабатывания (поллер + вебхук)
    if await asyncio.to_thread(is_file_processed, file_id):
        print(f"[process_new_document] {file_id} already processed — skip")
        return

    ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    if ext not in SUPPORTED_EXTS:
        print(f"[process_new_document] Unsupported type: .{ext}")
        return

    tmp_path = None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Fetch metadata + DOWNLOAD_URL
            r = await client.post(
                BITRIX_WEBHOOK_URL + "disk.file.get", json={"id": file_id}
            )
            r.raise_for_status()
            meta = r.json().get("result", {})
            download_url = meta.get("DOWNLOAD_URL")
            detail_url = meta.get("DETAIL_URL")
            update_time = str(meta.get("UPDATE_TIME") or "")
            if not download_url:
                print(f"[process_new_document] No DOWNLOAD_URL for {file_id}")
                return

            # 2. Download bytes
            r2 = await client.get(download_url)
            r2.raise_for_status()
            file_bytes = r2.content

        # 3. Save to temp path
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        tmp_path = os.path.join(data_dir, f"tmp_{file_name}")
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)

        # 4. Parse to chunks (sync, run in thread) — диспетчер форматов (№4)
        new_chunks = await asyncio.to_thread(parse_file, tmp_path, ext, file_name)

        if not new_chunks:
            print(f"[process_new_document] No chunks from {file_name}")
            return
        print(f"[process_new_document] {len(new_chunks)} chunks extracted")

        # 5. Embed new chunks
        texts = [c.get("text", "") for c in new_chunks]
        new_emb = await asyncio.to_thread(_embed_texts, texts)

        # 6. Влить в индекс через index_store (лок + атомарная запись).
        # Роль берётся из ПАПКИ (раскладка HR), не из содержимого документа.
        # В ролевые папки кладут стандарты для персонала → audience=staff;
        # гостевые тексты в старом индексе размечает scripts/backfill_roles.py.
        folder_key = str(folder_id or "")
        normalised = [
            {
                "text": c.get("text", ""),
                "heading": c.get("heading", file_name),
                "section": c.get("section", file_name),
                "roles": list(roles),
                "audience": "staff",
                "doc_name": file_name,
                "folder_id": folder_key,
            }
            for c in new_chunks
        ]
        # Замена версии: то же имя в ТОЙ ЖЕ папке → старые чанки долой.
        # (То же имя в ДРУГОЙ папке — копия для другой роли, оба набора живут.)
        is_replacement = await asyncio.to_thread(
            index_store.has_document, file_name, folder_key
        )
        removed = 0
        if is_replacement:
            removed = await asyncio.to_thread(
                index_store.remove_document, file_name, folder_key
            )
            print(f"[process_new_document] replacing old version: -{removed} chunks")
        total = await asyncio.to_thread(index_store.append_document, normalised, new_emb)

        chunks, embeddings = load_index()
        print(f"[process_new_document] RAG reloaded — {total} chunks total")

        # Замена «удалил+залил» (новый file_id): подчистить строку старого file_id
        # этого документа в этой папке, чтобы синк удалений не сработал по нему.
        if is_replacement and folder_key:
            for old in await asyncio.to_thread(get_processed_by_folders, [folder_key]):
                if old["doc_name"] == file_name and str(old["file_id"]) != str(file_id):
                    await asyncio.to_thread(remove_processed_file, str(old["file_id"]))

        # 6.5. Уже есть курс с этим doc_name: копия из другой папки ИЛИ замена
        # версии — второй курс не нужен, правки вопросов HR (№3) сохраняются.
        duplicate = await asyncio.to_thread(get_course_by_doc_name, file_name)
        if duplicate:
            if duplicate.get("archived_at"):
                await asyncio.to_thread(set_course_archived, duplicate["id"], False)
                print(f"[process_new_document] course {duplicate['id']} unarchived")
            await asyncio.to_thread(
                mark_file_processed, file_id, file_name, folder_id, update_time
            )
            if is_replacement:
                notify = (f"🔄 Документ «{file_name}» обновлён: "
                          f"{len(normalised)} фрагментов (было {removed}). "
                          "Вопросы курса сохранены.")
                for hr_id in _hr_ids():
                    await _send(str(hr_id), notify, HR_BOT_ID)
            print(
                f"[process_new_document] duplicate doc_name={file_name!r} "
                f"(course_id={duplicate['id']}) — chunks ingested for roles={roles}, "
                "course skipped"
            )
            return

        # 7. Generate questions
        from app.course_generator import generate_questions  # noqa: PLC0415

        questions = await asyncio.to_thread(generate_questions, file_name, new_chunks[:20])
        questions_json = json.dumps(questions, ensure_ascii=False)

        # 8. Persist to DB
        course_id = await asyncio.to_thread(
            save_draft_course, file_name, file_id, questions_json, detail_url
        )
        await asyncio.to_thread(
            mark_file_processed, file_id, file_name, folder_id, update_time
        )

        # 9. Save draft JSON
        courses_dir = os.path.join(data_dir, "courses")
        os.makedirs(courses_dir, exist_ok=True)
        draft_path = os.path.join(courses_dir, f"{file_id}_draft.json")
        with open(draft_path, "w", encoding="utf-8") as f:
            json.dump(questions, f, ensure_ascii=False, indent=2)

        # 10. Notify HR
        hr_ids = _hr_ids()
        notify_text = (
            f"📄 Новый документ: *{file_name}*\n"
            f"Сгенерировано 15 вопросов (5 базовых + 10 экзаменационных).\n\n"
            f"Посмотреть вопросы: Вопросы {course_id}\n"
            f"Активировать курс: Подтвердить {course_id}"
        )
        for hr_id in hr_ids:
            await _send(str(hr_id), notify_text, HR_BOT_ID)
        print(f"[process_new_document] DONE course_id={course_id}, HR notified: {hr_ids}")

    except Exception as exc:
        import traceback
        print(f"[process_new_document] ERROR: {exc}\n{traceback.format_exc()}")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _embed_texts(texts: list[str]) -> "np.ndarray":  # noqa: F821
    """Эмбеддинг чанков по одному (логика MVP). Модульная — мокается в тестах."""
    import numpy as np  # noqa: PLC0415
    from openai import OpenAI  # noqa: PLC0415

    oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    vecs = []
    for t in texts:
        resp = oai.embeddings.create(
            model="text-embedding-3-small", input=[t[:2000]]
        )
        vecs.append(resp.data[0].embedding)
    return np.array(vecs, dtype=np.float32)


async def _send_course_file(dialog_id: str, course_id: int) -> None:
    """Приложить файл документа в чат при старте курса (№4).

    Методы im.disk.* без детальных секций в выжимке доков — каскад попыток;
    любой фейл тихо игнорируется: ссылка на документ уже есть в тексте
    _start_reading, старт курса ломать нельзя."""
    try:
        course = await asyncio.to_thread(get_course_by_id, course_id)
        doc_id = (course or {}).get("doc_id")
        if not doc_id:
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                BITRIX_WEBHOOK_URL + "im.dialog.get", json={"DIALOG_ID": dialog_id}
            )
            r.raise_for_status()
            dialog = r.json().get("result") or {}
            chat_id = dialog.get("chat_id") or dialog.get("id") or dialog.get("ID")
            if not chat_id:
                print(f"[course-file] no chat_id for {dialog_id}: {dialog}")
                return
            for id_key in ("UPLOAD_ID", "DISK_ID"):
                r2 = await client.post(
                    BITRIX_WEBHOOK_URL + "im.disk.file.commit",
                    json={"CHAT_ID": chat_id, id_key: int(doc_id)},
                )
                if r2.status_code == 200 and r2.json().get("result"):
                    print(f"[course-file] sent doc {doc_id} to {dialog_id} ({id_key})")
                    return
                print(f"[course-file] {id_key} attempt failed: "
                      f"{r2.status_code} {r2.text[:200]}")
    except Exception as exc:
        print(f"[course-file] fallback to link: {exc!r}")
