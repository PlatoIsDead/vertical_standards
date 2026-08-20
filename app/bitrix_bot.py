import asyncio
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request

import app.index_store as index_store
from app import deadlines, gamification
from app.db import (
    activate_course_by_id,
    add_employee,
    add_manager,
    admit_session,
    get_active_courses,
    count_processed_by_doc_name,
    get_all_employees,
    get_course_by_doc_name,
    get_course_by_id,
    get_course_questions,
    get_employee_by_email,
    get_escalated,
    get_managers,
    get_meta,
    get_pending_courses,
    get_processed_by_folders,
    get_processed_by_hash,
    get_processed_file,
    get_report_rows,
    get_session,
    get_session_answers,
    get_session_dialog_id,
    get_sessions_by_user,
    get_user_departments,
    get_waiting_session,
    init_db,
    is_employee_allowed,
    is_file_processed,
    is_user_seen,
    mark_escalated,
    mark_file_processed,
    mark_user_seen,
    remove_manager,
    remove_processed_file,
    save_draft_course,
    seen_users_empty,
    set_can_switch_role,
    set_course_archived,
    set_meta,
    update_course_questions,
    update_session_by_user,
    update_user_departments,
)
from app.doc_parsers import SUPPORTED_EXTS, parse_file
from app.hr_tools import (
    REPLACEMENT_TEMPLATE,
    _norm_letter,
    apply_replacement,
    build_history_text,
    build_report_text,
    correct_option,
    format_question_card,
    parse_replacement,
    question_by_ref,
)
from app import keyboards
from app.rag import load_index
from app.report_excel import build_report_xlsx
from app.roles import (
    ALL_STAFF,
    display_name,
    load_roles_config,
    parse_filename,
    save_roles_config,
    selectable_roles,
)
from app.state_machine import (
    _MENU_COMMANDS,
    _last_known_role,
    _retake_fork_text,
    bare_dialog,
    course_roles,
    md_to_bb,
    my_courses,
    process_message,
    start_onboarding,
)

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
# №9: неделя на курс; напоминания раз в день; эскалация ступень 2 = 2 срока
ESCALATION_DAYS = int(os.getenv("ESCALATION_DAYS", "7"))
REMINDER_HOUR_MSK = int(os.getenv("REMINDER_HOUR_MSK", "9"))
ESCALATION_CHECK_INTERVAL = 3600
# №10: ПРИВАТНАЯ папка Диска для xlsx-отчётов (ФИО+результаты — не класть
# в MONITOR_FOLDER_ID, её видят сотрудники). Пусто = отчёт только текстом.
REPORTS_FOLDER_ID = os.getenv("REPORTS_FOLDER_ID", "")
# №7: инлайн-кнопки employee-бота — выключены до живой проверки на сервере
BUTTONS_ENABLED = os.getenv("BUTTONS_ENABLED", "0") == "1"

# Initialise DB and load RAG index at startup
init_db()
chunks, embeddings = load_index()


_poller_task = None
_user_poller_task = None
_rating_task = None
_reminder_task = None
_escalation_task = None
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
                        "отчёт", "отчет", "руководители", "руководитель",
                        "перегенерировать", "аббревиатура", "аббревиатуры",
                        "роль")


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
    global _reminder_task, _escalation_task
    print(f"[build] git commit: {os.getenv('GIT_COMMIT', 'dev')}")
    _poller_task = asyncio.create_task(_disk_poll_loop())
    _user_poller_task = asyncio.create_task(_user_poll_loop())
    _rating_task = asyncio.create_task(_weekly_rating_loop())
    _reminder_task = asyncio.create_task(_reminder_loop())
    _escalation_task = asyncio.create_task(_escalation_loop())


# ── №9: дедлайны — напоминания сотрудникам и эскалации руководителям ─────────

def _deadline_items() -> list[dict]:
    """Незакрытые назначения с дедлайнами (sync — звать через to_thread)."""
    baseline = get_meta("deadlines_baseline") or ""
    employees = get_all_employees()
    courses = get_active_courses()          # архивные уже исключены
    sessions_by_uid = {str(e["bitrix_uid"]):
                       get_sessions_by_user(str(e["bitrix_uid"]))
                       for e in employees}
    roles_by_uid = {str(e["bitrix_uid"]): _last_known_role(str(e["bitrix_uid"]))
                    for e in employees}
    return deadlines.assignments_with_deadlines(
        employees, courses, sessions_by_uid, roles_by_uid,
        baseline, datetime.utcnow(), ESCALATION_DAYS)


async def _reminder_loop():
    print(f"[reminder] Started — daily ~{REMINDER_HOUR_MSK}:00 МСК "
          f"(check hourly), deadline {ESCALATION_DAYS}d")
    while True:
        await asyncio.sleep(3600)
        try:
            await _maybe_send_reminders()
        except Exception as exc:
            print(f"[reminder] ERROR: {exc!r}")


async def _maybe_send_reminders(now_utc: "datetime | None" = None):
    """Раз в день после REMINDER_HOUR_MSK: каждому сотруднику — его
    непройденные курсы с остатком дней. Идемпотентность — meta-дата (МСК)."""
    now_utc = now_utc or datetime.utcnow()
    now_msk = now_utc + timedelta(hours=3)
    if now_msk.hour < REMINDER_HOUR_MSK:
        return
    today = now_msk.date().isoformat()
    if await asyncio.to_thread(get_meta, "reminder_last_date") == today:
        return
    items = await asyncio.to_thread(_deadline_items)
    by_uid: dict[str, list] = {}
    for it in items:
        by_uid.setdefault(it["uid"], []).append(it)
    sent = 0
    for uid, uid_items in by_uid.items():
        text = deadlines.build_reminder_text(uid_items)
        if text:
            await _send(f"u{uid}", text, BOT_ID,
                        **_kb_kwargs(await _session_keyboard(uid)))
            sent += 1
    # Метка ПОСЛЕ отправки: упали посреди — следующий час дошлёт (риск
    # частичного дубля меньше, чем потерянный день напоминаний)
    await asyncio.to_thread(set_meta, "reminder_last_date", today)
    print(f"[reminder] sent to {sent} employees")


async def _escalation_loop():
    print(f"[escalation] Started — stage1 {ESCALATION_DAYS}d / "
          f"stage2 {2 * ESCALATION_DAYS}d, check hourly")
    while True:
        await asyncio.sleep(ESCALATION_CHECK_INTERVAL)
        try:
            await _check_escalations()
        except Exception as exc:
            print(f"[escalation] ERROR: {exc!r}")


async def _check_escalations(now_utc: "datetime | None" = None):
    now_utc = now_utc or datetime.utcnow()
    items = await asyncio.to_thread(_deadline_items)
    if not items:
        return
    employees = {str(e["bitrix_uid"]): e
                 for e in await asyncio.to_thread(get_all_employees)}
    managers = await asyncio.to_thread(get_managers)
    for stage in (1, 2):
        already = await asyncio.to_thread(get_escalated, stage)
        due = deadlines.find_due_escalations(items, already, stage, now_utc,
                                             ESCALATION_DAYS)
        if not due:
            continue
        text = deadlines.build_escalation_message(due, employees, stage,
                                                  ESCALATION_DAYS)
        recipients = [m for m in managers
                      if m["level"] == (1 if stage == 1 else 2)]
        fallback_l1 = stage == 2 and not recipients
        if fallback_l1:
            recipients = [m for m in managers if m["level"] == 1]
        resolved, missing = [], []
        for m in recipients:
            # httpx.HTTPError НЕ ловим точечно: транзиентный сбой прерывает
            # весь цикл (уйдёт в _escalation_loop) — эскалации не помечены,
            # следующий час доставит
            user = await _bitrix_user_by_email(m["email"])
            (resolved if user else missing).append((m["email"], user))
        for _email, user in resolved:
            await _send(f"u{user['ID']}", text, HR_BOT_ID)
        if missing or not resolved or fallback_l1:
            note = ""
            if missing:
                note += ("⚠️ Не найдены на портале: "
                         + ", ".join(e for e, _ in missing) + "\n")
            if fallback_l1:
                note += ("ℹ️ Старших руководителей в реестре нет — "
                         "эскалация 2-й ступени уходит руководителям.\n")
            for hr_id in _hr_ids():
                await _send(str(hr_id), (note + "\n" + text) if note else text,
                            HR_BOT_ID)
        for it in due:                       # помечаем ПОСЛЕ доставки
            await asyncio.to_thread(mark_escalated, it["uid"],
                                    it["course"]["id"], stage)
        print(f"[escalation] stage {stage}: {len(due)} пар, "
              f"{len(resolved)} руководителей")


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
        if file_name.startswith("."):
            # macOS-мусор: .DS_Store и AppleDouble ._*.docx (бинарь с
            # расширением docx — ext-гейт ниже его пропустил бы).
            # Демо-фидбек E: заодно убираем в корзину Диска (Дмитрий подтвердил)
            asyncio.create_task(_trash_junk_file(file_id, file_name))
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


async def _trash_junk_file(file_id: str, file_name: str) -> None:
    """macOS-мусор → корзина Диска (markdeleted восстановим, в отличие от
    delete; мак пересоздаст файлы — Дмитрий в курсе). Best-effort: любая
    ошибка только логируется, поллер не ронять."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                BITRIX_WEBHOOK_URL + "disk.file.markdeleted", json={"id": file_id}
            )
        print(f"[poller] junk → корзина: {file_name!r} ({r.status_code})")
    except Exception as exc:
        print(f"[poller] junk delete failed for {file_name!r}: {exc!r}")


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


def _abbr_list_text(cfg: dict) -> str:
    prefixes = cfg.get("prefixes", {})
    if not prefixes:
        return "Реестр аббревиатур пуст."
    roles_map = cfg.get("roles", {})
    lines = ["🔤 Аббревиатуры департаментов:"]
    for abbr in sorted(prefixes):
        lines.append(f"• {abbr} → {roles_map.get(prefixes[abbr], prefixes[abbr])}")
    return "\n".join(lines)


def _hr_ids() -> list[str]:
    return [x.strip() for x in os.getenv("HR_USER_IDS", "").split(",") if x.strip()]


def _extract_email(text: str) -> str | None:
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    return m.group(0).lower() if m else None


# ── Визард правки вопроса (17.08: вместо one-shot всей карточки) ─────────────

_WIZARD_STEPS = ("text", "opt_a", "opt_b", "opt_c", "opt_d", "correct", "confirm")


def _wizard_step_text(pending: dict) -> str:
    d = pending["draft"]
    step = pending["step"]
    n = _WIZARD_STEPS.index(step) + 1
    if step == "text":
        return (f"✏️ Правка вопроса {pending['q_num']} курса "
                f"«{pending['doc_name']}» — шаг 1/6: текст вопроса.\n"
                f"Сейчас: {d['text']}\n\n"
                "Пришли новый текст, точку (.) — оставить как есть, либо весь "
                "вопрос целиком по шаблону:\n\n" + REPLACEMENT_TEMPLATE
                + "\n\nОтмена — выйти без изменений.")
    if step.startswith("opt_"):
        i = "abcd".index(step[-1])
        return (f"Шаг {n}/6: вариант {'ABCD'[i]}.\n"
                f"Сейчас: {d['options'][i]}\n\n"
                "Пришли новый текст варианта или точку (.) — оставить.")
    if step == "correct":
        return (f"Шаг 6/6: правильный ответ.\nСейчас: {d['correct']}\n\n"
                "Пришли букву A–D или точку (.) — оставить.")
    # confirm
    preview = format_question_card(pending["doc_name"], d, pending["q_num"])
    return ("Проверь вопрос:\n\n" + preview
            + "\n\n💾 Сохранить — записать · 🔄 Заново — с первого шага · "
              "Отмена — выйти без изменений.")


async def _wizard_input(user_id: str, pending: dict,
                        raw: str) -> tuple[str, list | None]:
    """Обработка ответа HR на текущем шаге визарда → (текст, клавиатура)."""
    d = pending["draft"]
    step = pending["step"]
    val = raw.strip()
    skip = val == "."

    if step == "confirm":
        low = val.lower().strip("💾🔄 ")
        if low == "сохранить":
            questions = await asyncio.to_thread(get_course_questions,
                                                pending["course_id"])
            try:
                questions = apply_replacement(questions, pending["q_num"], d)
            except ValueError as exc:
                return (f"❌ {exc}\n\n" + _wizard_step_text(pending),
                        keyboards.hr_wizard_confirm())
            await asyncio.to_thread(
                update_course_questions, pending["course_id"],
                json.dumps(questions, ensure_ascii=False),
            )
            course_id, q_num = pending["course_id"], pending["q_num"]
            saved = question_by_ref(questions, q_num)
            _pending_edits.pop(user_id, None)
            return (f"✅ Вопрос {q_num} сохранён.\n\n"
                    + format_question_card(pending["doc_name"], saved, q_num),
                    keyboards.hr_question_card(course_id, q_num))
        if low == "заново":
            pending["step"] = "text"
            return _wizard_step_text(pending), keyboards.hr_wizard_step()
        return _wizard_step_text(pending), keyboards.hr_wizard_confirm()

    if step == "text":
        repl = parse_replacement(raw)
        if repl is not None:              # power-user: цельный блок по шаблону
            d.update(repl)
            pending["step"] = "confirm"
            return _wizard_step_text(pending), keyboards.hr_wizard_confirm()
        if not skip:
            d["text"] = val
    elif step.startswith("opt_"):
        i = "abcd".index(step[-1])
        if not skip:
            d["options"][i] = f"{'ABCD'[i]}. {val}"
    elif step == "correct" and not skip:
        letter = _norm_letter(val)
        if letter not in ("A", "B", "C", "D"):
            return ("❌ Нужна буква A–D (или точка — оставить).\n\n"
                    + _wizard_step_text(pending), keyboards.hr_wizard_step())
        d["correct"] = letter

    pending["step"] = _WIZARD_STEPS[_WIZARD_STEPS.index(step) + 1]
    pending["expires"] = time.monotonic() + PENDING_EDIT_TTL
    kb = (keyboards.hr_wizard_confirm() if pending["step"] == "confirm"
          else keyboards.hr_wizard_step())
    return _wizard_step_text(pending), kb


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
    kb = None
    if transfer:
        text = f"🔄 {fio} сменил отдел. Если поменялась роль — пусть напишет боту «Роль»."
    else:
        text = f"👤 Новый сотрудник: {fio} ({email or 'email не указан'})."
        if email:
            text += f"\nПригласить в обучение: Пригласить {email}"
            kb = keyboards.hr_invite(email) if BUTTONS_ENABLED else None
    for hr_id in _hr_ids():
        await _send(str(hr_id), text, HR_BOT_ID, **_kb_kwargs(kb))


def _kb_kwargs(keyboard: list | None) -> dict:
    """kwargs для _send: ключ keyboard — только при реальной клавиатуре.
    Тестовые двойники _send без параметра keyboard остаются валидными,
    а при выключенном флаге вызовы _send не меняются вовсе."""
    return {"keyboard": keyboard} if keyboard else {}


async def _send(dialog_id: str, text: str, bot_id: str = None,
                client_id: str = "", keyboard: list | None = None) -> None:
    # Network to portal.becar.ru flaps (ConnectTimeout then 200 within seconds),
    # so retry with backoff to land in a live window instead of dropping the reply.
    dialog_id = bare_dialog(dialog_id)   # «uNNN» → 400 DIALOG_ID_EMPTY
    bot_id = bot_id or BOT_ID
    # Proactive sends pass no client_id → fall back to the bot's token from .env,
    # otherwise Bitrix rejects imbot.message.add with 403.
    if not client_id:
        client_id = HR_CLIENT_ID if str(bot_id) == str(HR_BOT_ID) else BOT_CLIENT_ID
    payload = {
        "BOT_ID": bot_id,
        "DIALOG_ID": dialog_id,
        "MESSAGE": md_to_bb(text),   # Bitrix понимает BB-код, не markdown
        "CLIENT_ID": client_id,
    }
    if keyboard:                     # №7: ключ отсутствует вовсе при None
        payload["KEYBOARD"] = keyboard
    last_exc = None
    for attempt in range(1, 6):  # up to 5 attempts: backoff 2,4,6,8s
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    BITRIX_WEBHOOK_URL + "imbot.message.add", json=payload
                )
            err = "" if resp.status_code == 200 else f" {resp.text[:200]}"
            print(f"BITRIX SEND: {resp.status_code} → {dialog_id} "
                  f"(attempt {attempt}){err}")
            return
        except Exception as exc:
            last_exc = exc
            print(f"BITRIX SEND retry {attempt}/5 (dialog={dialog_id}): {exc!r}")
            if attempt < 5:
                await asyncio.sleep(2 * attempt)
    print(f"BITRIX SEND ERROR after 5 attempts (dialog={dialog_id}): {last_exc!r}")


_OPT_BODY_RE = re.compile(r"^[A-D][.)]\s*(.*)$", re.DOTALL)


async def _test_options(session: dict | None) -> list | None:
    """Тела вариантов текущего вопроса (BASIC_TEST/EXAM) — для BLOCK-кнопок
    «A · текст» (дизайн 17.08: все тела ≤24 симв. → нажатие без сверки)."""
    if not session or session["state"] not in ("BASIC_TEST", "EXAM"):
        return None
    questions = await asyncio.to_thread(get_course_questions,
                                        session["course_id"])
    phase = "basic" if session["state"] == "BASIC_TEST" else "exam"
    q_list = questions.get(f"{phase}_questions", [])
    idx = session.get("current_q_idx", 0)
    if not (0 <= idx < len(q_list)):
        return None
    bodies = []
    for o in q_list[idx].get("options", []):
        m = _OPT_BODY_RE.match(str(o).strip())
        if not m:
            return None
        bodies.append(m.group(1).strip())
    return bodies if len(bodies) == 4 else None


async def _session_keyboard(user_id: str) -> list | None:
    """Клавиатура по ТЕКУЩЕМУ состоянию сессии — для проактивных отправок
    (напоминания №9): кнопки всегда совпадают с тем, что FSM сейчас поймёт."""
    if not BUTTONS_ENABLED:
        return None
    session = await asyncio.to_thread(get_session, user_id)
    fork = False
    if session is None:
        fork = (await asyncio.to_thread(_retake_fork_text, user_id)) is not None
    return keyboards.for_session(session, fork, selectable_roles(),
                                 test_options=await _test_options(session))


async def _handle_employee_message(user_id: str, question: str,
                                   dialog_id: str, client_id: str,
                                   bot_id: str = None,
                                   dedup_key: str | None = None) -> None:
    """Общий путь employee-бота: текст из чата ("/") и нажатие кнопки
    ("/command", №7) обрабатываются одинаково. dedup_key — ключ дедупа для
    кнопок (текст+MESSAGE_ID): та же надпись на ДРУГОМ сообщении легитимна."""
    if _is_duplicate(user_id, dialog_id, dedup_key or question):
        print(f"DEDUP skip (dialog={dialog_id}): {question!r}")
        return

    # №11: сессия может появиться с course_id=0 (этап выбора роли) — файл
    # документа шлём в момент НАЗНАЧЕНИЯ курса, а не появления сессии.
    before = await asyncio.to_thread(get_session, user_id)
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

    # Курс назначен этим сообщением → приложить файл документа (№4)
    after = None
    if not (before and before["course_id"]):
        after = await asyncio.to_thread(get_session, user_id)
        if after and after["course_id"]:
            asyncio.create_task(
                _send_course_file(dialog_id, after["course_id"])
            )

    # №7: клавиатура — по СВЕЖЕМУ состоянию сессии (FSM не трогаем)
    keyboard = None
    if BUTTONS_ENABLED:
        if after is None:
            after = await asyncio.to_thread(get_session, user_id)
        fork = False
        if after is None:
            fork = (await asyncio.to_thread(_retake_fork_text, user_id)
                    is not None)
        keyboard = keyboards.for_session(after, fork, selectable_roles(),
                                         test_options=await _test_options(after))
        # Ответ на «Мои курсы» → курсы BLOCK-кнопками (дизайн 17.08;
        # в WAITING_HR переключение тоже разрешено)
        if (after and after["state"] in ("READING", "WAITING_HR")
                and question.strip().lower() in _MENU_COMMANDS):
            _, selectable = await asyncio.to_thread(
                my_courses, user_id, after.get("role"))
            if selectable:
                keyboard = keyboards.courses_menu(
                    [(c["n"], display_name(c["doc_name"]), c["status"])
                     for c in selectable],
                    reading=(after["state"] == "READING"))

    asyncio.create_task(_send(dialog_id, text, bot_id or BOT_ID, client_id,
                              keyboard=keyboard))


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

    await _handle_employee_message(user_id, question, dialog_id, client_id,
                                   bot_id)
    return {"status": "ok"}


@app.post("/command")
async def command_handler(request: Request):
    """ONIMCOMMANDADD: нажатие кнопки (№7). Формат полей в выжимке доков не
    описан — логируем ВСЁ, поля достаём двумя известными путями; расхождение
    правится по живому логу."""
    form = await request.form()
    event = (form.get("event") or "").upper()
    print(f"[command] event={event!r} fields={dict(form)}")
    if event != "ONIMCOMMANDADD":
        return {"status": "ignored"}

    def field(name: str) -> str:
        # Живой формат (лог 17.08): индекс в ключе = COMMAND_ID, не 0
        # (data[COMMAND][71][COMMAND_PARAMS]) — ищем по суффиксу ключа;
        # фолбэк — плоский data[PARAMS][...].
        for k in form:
            if k.startswith("data[COMMAND][") and k.endswith(f"][{name}]"):
                return (form.get(k) or "").strip()
        return (form.get(f"data[PARAMS][{name}]") or "").strip()

    params = field("COMMAND_PARAMS")
    dialog_id = field("DIALOG_ID")
    user_id = field("USER_ID") or (form.get("data[USER][ID]") or "").strip()
    client_id = form.get("auth[application_token]", "")

    if not params or not dialog_id or not user_id:
        return {"status": "no_data"}

    # Дедуп по (текст + сообщение-источник): двойной клик той же кнопки
    # гасится, та же надпись на другом сообщении — проходит.
    message_id = field("MESSAGE_ID")
    dedup_key = f"{params}#msg{message_id}" if message_id else None

    # Роутинг по ИМЕНИ команды: hrsay → HR-бот, say/нет поля → employee
    command_name = field("COMMAND").lower()
    if command_name == "hrsay":
        await _handle_hr_message(user_id, params, dialog_id, client_id,
                                 dedup_key=dedup_key)
    else:
        await _handle_employee_message(user_id, params, dialog_id, client_id,
                                       dedup_key=dedup_key)
    return {"status": "ok"}


# ── Рассылка «доступен новый курс» при активации (демо-фидбек A) ─────────────

def _course_recipients(course: dict) -> list[str]:
    """Кому анонсировать активированный курс (sync — звать через to_thread):
    в whitelist, БЕЗ активной сессии, курс не пройден, роль пересекается с
    ролями курса. Роль неизвестна (ни разу не выбирал) → только ALL-курсы,
    иначе ролевые курсы спамят не тем людям (до №8 роль-из-отдела)."""
    croles = set(course_roles(course))
    uids = []
    for e in get_all_employees():
        uid = e["bitrix_uid"]
        if get_session(uid):                      # busy: текущий курс/выбор роли
            continue
        done = {s["course_id"] for s in get_sessions_by_user(uid)
                if s["state"] == "DONE"}
        if course["id"] in done:
            continue
        role = _last_known_role(uid)
        if role and ({role, ALL_STAFF} & croles):
            uids.append(uid)
        elif role is None and ALL_STAFF in croles:
            uids.append(uid)
    return uids


async def _broadcast_course(course: dict, uids: list[str]) -> None:
    """Анонс от EMPLOYEE-бота (BOT_ID): client_id не передаём — _send возьмёт
    BOT_CLIENT_ID из .env. Последовательно: _send ретраит до 5 раз на флапе."""
    text = (f"📚 Тебе доступен новый курс: *{display_name(course['doc_name'])}*\n"
            "Напиши мне любое сообщение, чтобы начать обучение.\n"
            f"⏰ На прохождение — {ESCALATION_DAYS} дней.")
    kb = keyboards.start_button("▶ Начать обучение") if BUTTONS_ENABLED else None
    for uid in uids:
        await _send(f"u{uid}", text, BOT_ID, **_kb_kwargs(kb))


def _standard_recipients(doc_roles: list[str]) -> list[str]:
    """Кому анонсировать НОВЫЙ СТАНДАРТ (19.08: доступен для вопросов сразу).
    Без гейтов busy/done — это знание, а не назначение курса. Роль неизвестна
    → только ALL-документы (не спамить ролевыми не тем людям)."""
    doc = set(doc_roles)
    uids = []
    for e in get_all_employees():
        uid = e["bitrix_uid"]
        role = _last_known_role(uid)
        if role and ({role, ALL_STAFF} & doc):
            uids.append(uid)
        elif role is None and ALL_STAFF in doc:
            uids.append(uid)
    return uids


async def _broadcast_new_standard(file_name: str, detail_url: str | None,
                                  doc_roles: list[str]) -> None:
    """«Стандарт доступен сразу после загрузки» — тест назначается позже,
    после «Подтвердить» (флоу 19.08). Fire-and-forget, _send сам ретраит."""
    uids = await asyncio.to_thread(_standard_recipients, doc_roles)
    text = (f"📄 Новый стандарт доступен: *{display_name(file_name)}*\n"
            + (f"{detail_url}\n" if detail_url else "")
            + "Уже можешь читать его и задавать мне вопросы по нему. "
              "Тест будет назначен позже.")
    for uid in uids:
        await _send(f"u{uid}", text, BOT_ID)


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

    await _handle_hr_message(user_id, question, dialog_id, client_id)
    return {"status": "ok"}


async def _handle_hr_message(user_id: str, question: str,
                             dialog_id: str, client_id: str,
                             dedup_key: str | None = None) -> None:
    """Общий путь HR-бота: текст из чата ("/hr") и нажатие HR-кнопки
    ("/command", команда hrsay) обрабатываются одинаково."""
    # Внутри визарда ключ дедупа включает шаг: «.» на соседних шагах легальна,
    # дубль-доставка ответа на ОДИН шаг гасится.
    wiz = _get_pending(user_id)
    key = dedup_key or question
    if wiz:
        key = f"{key}#step{wiz.get('step', '')}"
    if _is_duplicate(user_id, dialog_id, key):
        print(f"DEDUP skip HR (dialog={dialog_id}): {question!r}")
        return

    # Гейт: HR-боту командуют только HR (пустой HR_USER_IDS = гейт выключен,
    # dev). Кнопки идут тем же путём — чужое нажатие отбивается здесь же.
    hr_ids = _hr_ids()
    if hr_ids and user_id not in hr_ids:
        print(f"HR GATE reject user={user_id!r}")
        asyncio.create_task(_send(
            dialog_id, "⛔ Команды HR-бота доступны только HR-менеджерам.",
            HR_BOT_ID, client_id,
        ))
        return

    kb: list | None = None  # клавиатура ветки; уходит только при BUTTONS_ENABLED
    msg_lower = question.lower()

    # ── Двухшаговая правка вопроса: живой pending перехватывает сообщение ────
    pending = _get_pending(user_id)
    if pending:
        if msg_lower in ("отмена", "cancel"):
            _pending_edits.pop(user_id, None)
            asyncio.create_task(_send(dialog_id, "Правка отменена.",
                                      HR_BOT_ID, client_id))
            return
        if not msg_lower.startswith(_HR_COMMAND_PREFIXES):
            text, kb = await _wizard_input(user_id, pending, question)
            asyncio.create_task(_send(
                dialog_id, text, HR_BOT_ID, client_id,
                **_kb_kwargs(kb if BUTTONS_ENABLED else None)))
            return
        _pending_edits.pop(user_id, None)  # команда важнее забытой правки

    m_courses = re.match(r"^(?:курсы|курс|список)(?:\s+(\d+))?$", msg_lower)
    if m_courses:
        # Дизайн 17.08: постранично по 8 — 40 кнопок в 30 КБ влезут,
        # но список перестаёт читаться; «Показать ещё» шлёт «Курсы {page+1}»
        page = int(m_courses.group(1) or 1)
        pending = await asyncio.to_thread(get_pending_courses)
        if not pending:
            text = "✅ Нет курсов, ожидающих активации."
        else:
            start = (page - 1) * keyboards.HR_COURSES_PAGE
            page_items = pending[start:start + keyboards.HR_COURSES_PAGE]
            if not page_items:                       # кривая страница → первая
                page, start = 1, 0
                page_items = pending[:keyboards.HR_COURSES_PAGE]
            header = "📋 *Курсы на проверке"
            if len(pending) > keyboards.HR_COURSES_PAGE:
                header += f" ({start + 1}–{start + len(page_items)} из {len(pending)})"
            lines = [header + ":*\n"]
            for c in page_items:
                lines.append(f"*{c['doc_name']}*")
                lines.append(f"  👁 Вопросы {c['id']}   ✅ Подтвердить {c['id']}\n")
            text = "\n".join(lines)
            kb = keyboards.hr_course_list(
                [c["id"] for c in page_items], page,
                remaining=len(pending) - start - len(page_items))

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
                    if ok:
                        # Рассылка fire-and-forget: HR не ждёт K×5 ретраев _send
                        uids = await asyncio.to_thread(_course_recipients, course)
                        asyncio.create_task(_broadcast_course(course, uids))
                        text = (
                            f"✅ Курс «{course['doc_name']}» активирован. "
                            + (f"Рассылаю уведомления: {len(uids)} чел."
                               if uids else
                               "Подходящих сотрудников для уведомления нет.")
                        )
                    else:
                        text = f"❌ Не удалось активировать курс №{course_id}."
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
                    await asyncio.to_thread(
                        add_employee, uid, email, fio, user_id,
                        (user.get("WORK_POSITION") or "").strip() or None)
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
                            new_session = await asyncio.to_thread(get_session, uid)
                            start_kb = (keyboards.for_session(
                                new_session, False, selectable_roles())
                                if BUTTONS_ENABLED else None)
                            asyncio.create_task(_send(f"u{uid}", first_msg,
                                                      BOT_ID,
                                                      **_kb_kwargs(start_kb)))
                            # №11: course_id=0 = этап выбора роли, файл уйдёт
                            # после выбора (хук в bot_handler)
                            if new_session and new_session["course_id"]:
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
            # 17.08: допуск целится в WAITING_HR-строку (свойство курса), а не
            # в активную сессию — сотрудник мог уйти проходить другой курс.
            waiting = await asyncio.to_thread(get_waiting_session, target_uid)
            if waiting:
                await asyncio.to_thread(admit_session, waiting["id"])
                active = await asyncio.to_thread(get_session, target_uid)
                in_place = bool(active and active["id"] == waiting["id"])
                course = await asyncio.to_thread(get_course_by_id,
                                                 waiting["course_id"])
                cname = (display_name(course["doc_name"]) if course
                         else f"курс №{waiting['course_id']}")
                if in_place:
                    msg = ("🎓 HR допустил тебя к экзамену! "
                           "Напиши что-нибудь, чтобы начать.")
                    kbd = (keyboards.start_button("🎓 Начать экзамен")
                           if BUTTONS_ENABLED else None)
                else:
                    # Не выдёргиваем из текущего курса — экзамен ждёт в меню
                    msg = (f"🎓 HR допустил тебя к экзамену по курсу *{cname}*. "
                           "Сдай его, когда будешь готов: Мои курсы → Выбрать.")
                    kbd = (keyboards.start_button("📚 Мои курсы", "Мои курсы", role="secondary")
                           if BUTTONS_ENABLED else None)
                if waiting.get("dialog_id"):
                    await _send(waiting["dialog_id"], msg, BOT_ID, client_id,
                                **_kb_kwargs(kbd))
                text = f"✅ Сотрудник {target_uid} допущен к экзамену ({cname})."
            else:
                # Легаси-форс: строки WAITING_HR нет — активную сессию в EXAM
                found = await asyncio.to_thread(update_session_by_user,
                                                target_uid, "EXAM", 0)
                if found:
                    emp_dialog = await asyncio.to_thread(get_session_dialog_id,
                                                         target_uid)
                    if emp_dialog:
                        start_kb = (keyboards.start_button("🎓 Начать экзамен")
                                    if BUTTONS_ENABLED else None)
                        await _send(
                            emp_dialog,
                            "🎓 HR допустил тебя к экзамену! Напиши что-нибудь, чтобы начать.",
                            BOT_ID, client_id,
                            **_kb_kwargs(start_kb),
                        )
                    text = f"✅ Сотрудник {target_uid} допущен к экзамену."
                else:
                    text = f"❌ Активная сессия для сотрудника {target_uid} не найдена."

    elif msg_lower.startswith("вопросы"):
        # 17.08: «Вопросы N» — карточки по одному; «N.q» — конкретный;
        # «N все» — прежняя простыня со всеми ответами
        m = re.match(r"^вопросы\s+(\d+)(?:\.(\d+))?(\s+все)?$", msg_lower)
        if not m:
            text = ("❌ Формат: Вопросы {N} — по карточке, "
                    "Вопросы {N}.{вопрос 1–15} — конкретный, "
                    "Вопросы {N} все — списком")
        else:
            course_id = int(m.group(1))
            q_num = int(m.group(2)) if m.group(2) else 1
            course = await asyncio.to_thread(get_course_by_id, course_id)
            if not course:
                text = f"❌ Курс №{course_id} не найден."
            elif m.group(3):                     # «все» — простыня
                questions = await asyncio.to_thread(get_course_questions, course_id)
                basic = questions.get("basic_questions", [])
                exam = questions.get("exam_questions", [])
                # Сквозная нумерация 1–15: 1–5 базовые, 6–15 экзамен
                # (та же, что в команде «Изменить»)
                lines = [f"📋 *{course['doc_name']}*\n"]
                lines.append("*Базовые вопросы (1–5):*")
                for i, q in enumerate(basic, 1):
                    lines.append(f"{i}. {q['text']}\n   → {correct_option(q)}")
                lines.append("\n*Экзаменационные вопросы (6–15):*")
                for i, q in enumerate(exam, 6):
                    lines.append(f"{i}. {q['text']}\n   → {correct_option(q)}")
                lines.append(f"\nДля активации: Подтвердить {course_id}")
                lines.append(f"Изменить вопрос: Изменить {course_id}.{{номер 1–15}}")
                text = "\n".join(lines)
                kb = keyboards.hr_course_actions(course_id)
            else:
                questions = await asyncio.to_thread(get_course_questions, course_id)
                q = question_by_ref(questions, q_num)
                if q is None:
                    text = "❌ Номер вопроса — от 1 до 15."
                else:
                    text = format_question_card(course["doc_name"], q, q_num)
                    kb = keyboards.hr_question_card(course_id, q_num)

    elif msg_lower.startswith("изменить"):
        # 17.08: пошаговый визард вместо one-shot; форматы «N q» и «N.q»
        m = re.match(r"^изменить\s+(\d+)[.\s]+(\d+)$", msg_lower)
        if not m:
            text = "❌ Формат: Изменить {курс}.{вопрос 1–15} (или через пробел)"
        else:
            course_id, q_num = int(m.group(1)), int(m.group(2))
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
                        "doc_name": course["doc_name"], "step": "text",
                        "draft": {"text": q["text"],
                                  "options": list(q["options"]),
                                  "correct": q["correct"],
                                  "explanation": q.get("explanation", "")},
                        "expires": time.monotonic() + PENDING_EDIT_TTL,
                    }
                    text = _wizard_step_text(_pending_edits[user_id])
                    kb = keyboards.hr_wizard_step()

    elif msg_lower.startswith("перегенерировать"):
        m = re.match(r"^перегенерировать\s+(\d+)(?:\.(\d+))?$", msg_lower)
        if not m:
            text = ("❌ Формат: Перегенерировать {N} — весь курс, "
                    "Перегенерировать {N}.{вопрос 1–15} — один вопрос")
        else:
            course_id = int(m.group(1))
            q_num = int(m.group(2)) if m.group(2) else None
            course = await asyncio.to_thread(get_course_by_id, course_id)
            if not course:
                text = f"❌ Курс №{course_id} не найден."
            elif q_num is not None and not (1 <= q_num <= 15):
                text = "❌ Номер вопроса — от 1 до 15."
            else:
                doc_chunks = [c for c in chunks
                              if c.get("doc_name") == course["doc_name"]]
                if not doc_chunks:
                    text = ("❌ Фрагменты документа не найдены в индексе — "
                            "перегенерация недоступна.")
                else:
                    # LLM долго — вебхук не ждёт (паттерн _build_and_send_report)
                    asyncio.create_task(_regenerate_and_notify(
                        dialog_id, client_id, course, q_num, doc_chunks))
                    what = (f"вопрос {q_num}" if q_num else "все 15 вопросов")
                    text = (f"⏳ Генерирую заново {what} курса "
                            f"«{course['doc_name']}» — пришлю результат…")

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
                    inner = ", ".join(x for x in (emp.get("work_position"),
                                                  emp.get("email")) if x)
                    label = f"{emp.get('full_name') or uid} ({inner or '—'})"
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
        # №10: полная матрица — файлом (текст выше усечён 30 строками)
        if REPORTS_FOLDER_ID:
            asyncio.create_task(_build_and_send_report(dialog_id))
            text += "\n\n📎 Полная матрица — сейчас пришлю файлом Excel."
        else:
            text += ("\n\n⚙️ Укажи REPORTS_FOLDER_ID в .env (приватная папка "
                     "Диска) — буду присылать полную матрицу файлом Excel.")

    elif msg_lower.startswith("руководител"):
        parts = question.split()
        email = _extract_email(question)
        sub = parts[1].lower() if len(parts) >= 2 else ""
        if len(parts) == 1:
            managers = await asyncio.to_thread(get_managers)
            if managers:
                mlines = ["👥 Руководители (получают эскалации):"]
                for m in managers:
                    tag = " (старший)" if m["level"] == 2 else ""
                    mlines.append(f"• {m['email']}{tag}")
                text = "\n".join(mlines)
            else:
                text = "Реестр руководителей пуст."
        elif sub == "добавить" and email:
            level = 2 if "старш" in msg_lower else 1
            added = await asyncio.to_thread(add_manager, email, user_id, level)
            text = (f"✅ {email} добавлен{' (старший)' if level == 2 else ''}."
                    if added else f"ℹ️ {email} уже в списке.")
        elif sub == "удалить" and email:
            removed = await asyncio.to_thread(remove_manager, email)
            text = (f"✅ {email} удалён." if removed
                    else f"⚠️ {email} не найден в списке.")
        else:
            text = ("❌ Формат: Руководители | Руководитель добавить {email} "
                    "[старший] | Руководитель удалить {email}")

    elif msg_lower.startswith(("аббревиатура", "аббревиатуры")):
        # 19.08: реестр аббревиатур департаментов правится из HR-бота
        cfg = await asyncio.to_thread(load_roles_config)
        prefixes = cfg.setdefault("prefixes", {})
        m = re.match(r"^аббревиатура\s+(добавить|удалить)\s+(\S{1,10})"
                     r"(?:\s+(.+))?$", question.strip(), re.IGNORECASE)
        if m:
            action = m.group(1).lower()
            abbr = m.group(2).upper()
            title = (m.group(3) or "").strip()
            if not re.fullmatch(r"[A-Z0-9]{1,6}", abbr):
                text = ("❌ Аббревиатура — латиница/цифры, до 6 символов "
                        "(FO, HSKP, FIN).")
            elif action == "удалить":
                if abbr in prefixes:
                    prefixes.pop(abbr)
                    await asyncio.to_thread(save_roles_config, cfg)
                    text = (f"✅ Аббревиатура {abbr} удалена (роль осталась "
                            "в реестре).\n\n" + _abbr_list_text(cfg))
                else:
                    text = f"⚠️ Аббревиатуры {abbr} нет в реестре."
            elif abbr in prefixes:
                known = cfg.get("roles", {}).get(prefixes[abbr], prefixes[abbr])
                text = f"ℹ️ {abbr} уже занята: {known}."
            else:
                slug = abbr.lower()
                roles_map = cfg.setdefault("roles", {})
                if slug not in roles_map and not title:
                    text = ("❌ Для новой роли укажи название: "
                            "Аббревиатура добавить FIN Финансовая служба")
                else:
                    if title:
                        roles_map.setdefault(slug, title)
                    prefixes[abbr] = slug
                    await asyncio.to_thread(save_roles_config, cfg)
                    text = (f"✅ {abbr} → {roles_map[slug]}.\n\n"
                            + _abbr_list_text(cfg))
        else:
            text = (_abbr_list_text(cfg) + "\n\n"
                    "Правка: Аббревиатура добавить {ABBR} {Название роли} · "
                    "Аббревиатура удалить {ABBR}")

    elif re.match(r"^роль\s+(разрешить|запретить)\b", msg_lower):
        # 19.08: право смены роли — например, менеджеру смотреть ответы
        # разных отделов; снятие возвращает роль по отделу
        m = re.match(r"^роль\s+(разрешить|запретить)\s+(\S+)$", msg_lower)
        if not m:
            text = "❌ Формат: Роль разрешить {email или ID} · Роль запретить {email или ID}"
        else:
            allow = m.group(1) == "разрешить"
            target = m.group(2)
            if "@" in target:
                emp = await asyncio.to_thread(get_employee_by_email, target)
                target = emp["bitrix_uid"] if emp else None
            if target is None:
                text = (f"⚠️ {m.group(2)} не найден среди приглашённых. "
                        f"Сначала: Пригласить {m.group(2)}")
            elif await asyncio.to_thread(set_can_switch_role, target, allow):
                text = (f"✅ Сотрудник {target} "
                        + ("может выбирать роль сам (команда «Роль»)."
                           if allow else
                           "больше не выбирает роль — она определяется отделом."))
            else:
                text = (f"⚠️ Сотрудник {target} не найден среди приглашённых. "
                        "Сначала: Пригласить {email}")

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
            "• *Отчёт* — сводка: кто прошёл обучение и с каким результатом\n"
            "• *Руководители* — реестр получателей эскалаций\n"
            "• *Руководитель добавить/удалить {email} [старший]* — правка реестра"
        )
        has_pending = bool(await asyncio.to_thread(get_pending_courses))
        kb = keyboards.hr_main_menu(has_pending)

    if not BUTTONS_ENABLED:
        kb = None
    asyncio.create_task(_send(dialog_id, text, HR_BOT_ID, client_id,
                              **_kb_kwargs(kb)))


async def _regenerate_and_notify(dialog_id: str, client_id: str, course: dict,
                                 q_num: int | None,
                                 doc_chunks: list[dict]) -> None:
    """Фоновая перегенерация вопросов (17.08). Best-effort: сбой — лог + «❌»."""
    from app import course_generator  # noqa: PLC0415
    try:
        if q_num is None:
            new_questions = await asyncio.to_thread(
                course_generator.generate_questions, course["doc_name"],
                doc_chunks)
            await asyncio.to_thread(
                update_course_questions, course["id"],
                json.dumps(new_questions, ensure_ascii=False))
            first = question_by_ref(new_questions, 1)
            text = (f"✅ Курс «{course['doc_name']}»: 15 вопросов пересозданы "
                    "(прежние, включая ручные правки, затёрты).\n\n"
                    + format_question_card(course["doc_name"], first, 1))
            kb = keyboards.hr_question_card(course["id"], 1)
        else:
            questions = await asyncio.to_thread(get_course_questions,
                                                course["id"])
            facts = (questions.get("facts_basic", [])
                     + questions.get("facts_exam", []))
            fact = facts[q_num - 1] if len(facts) >= q_num else None
            existing = [x["text"] for x in
                        questions.get("basic_questions", [])
                        + questions.get("exam_questions", [])]
            new_q = await asyncio.to_thread(
                course_generator.regenerate_one, course["doc_name"],
                doc_chunks, fact, existing)
            questions = apply_replacement(questions, q_num, {
                "text": new_q["text"], "options": new_q["options"],
                "correct": new_q["correct"],
                "explanation": new_q.get("explanation", ""),
            })
            await asyncio.to_thread(
                update_course_questions, course["id"],
                json.dumps(questions, ensure_ascii=False))
            text = (f"✅ Вопрос {q_num} курса «{course['doc_name']}» "
                    "пересоздан.\n\n"
                    + format_question_card(course["doc_name"], new_q, q_num))
            kb = keyboards.hr_question_card(course["id"], q_num)
        await _send(dialog_id, text, HR_BOT_ID, client_id,
                    **_kb_kwargs(kb if BUTTONS_ENABLED else None))
    except Exception as exc:
        import traceback
        print(f"[regen] ERROR: {exc}\n{traceback.format_exc()}")
        await _send(dialog_id,
                    "❌ Перегенерация не удалась — попробуй ещё раз позже.",
                    HR_BOT_ID, client_id)


@app.post("/disk-webhook")
async def disk_webhook(request: Request):
    form = await request.form()
    # Имена событий приходят UPPERCASE (как в /user-webhook) — 19.08
    event = (form.get("event") or "").upper()

    # Log all fields to help debug payload structure in production
    print(f"[disk-webhook] event={event!r} fields={dict(form)}")

    if event != "ONDISKFILEADD":
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


_ingest_locks: dict[str, asyncio.Lock] = {}


def _doc_lock(file_name: str) -> asyncio.Lock:
    return _ingest_locks.setdefault(file_name, asyncio.Lock())


async def process_new_document(file_id: str, file_name: str,
                               roles: list[str] = None,
                               folder_id: str = None) -> None:
    """Download from Bitrix Disk, update RAG index, generate questions, notify HR.

    roles — роли-адресаты чанков (из папки); префиксы в имени файла главнее (№11)."""
    print(f"[process_new_document] START file_id={file_id!r} name={file_name!r} roles={roles}")

    if file_name.startswith("."):
        # macOS-мусор (.DS_Store, AppleDouble ._*.docx) — путь вебхука
        print(f"[process_new_document] hidden/junk file — skip: {file_name!r}")
        return

    # №11: роли из префиксов имени файла («FO, RES Название.docx»), фолбэк — папка
    parsed = parse_filename(file_name)
    if parsed["roles"]:
        roles = parsed["roles"]
    roles = roles or ["all_staff"]

    ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    if ext not in SUPPORTED_EXTS:
        print(f"[process_new_document] Unsupported type: .{ext}")
        return

    # Гонка копий одного документа (живые данные 29.07: 4 курса
    # «КОНФЕДЕНЦИАЛЬНОСТЬ» за 4с из 4 копий в разных ролевых папках —
    # каждая проверила «курс уже есть?» до сохранения первой): копии
    # сериализуются per-doc локом, разные документы идут параллельно.
    async with _doc_lock(file_name):
        await _ingest_document(file_id, file_name, roles, folder_id, parsed, ext)


async def _ingest_document(file_id: str, file_name: str, roles: list[str],
                           folder_id: str, parsed: dict, ext: str) -> None:
    """Тело ингеста — строго под _doc_lock(file_name)."""
    global chunks, embeddings

    # Защита от двойного срабатывания (поллер + вебхук)
    if await asyncio.to_thread(is_file_processed, file_id):
        print(f"[process_new_document] {file_id} already processed — skip")
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
        # 17.08: дедуп переименованных копий — хеш сырого содержимого
        content_hash = hashlib.sha256(file_bytes).hexdigest()

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
                mark_file_processed, file_id, file_name, folder_id, update_time,
                content_hash,
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

        # 6.6. То же СОДЕРЖИМОЕ под другим именем (17.08): курс не плодим —
        # авто-привязка к существующему (решение юзера), HR в курсе.
        same = await asyncio.to_thread(get_processed_by_hash, content_hash)
        if same and same["doc_name"] != file_name:
            twin = await asyncio.to_thread(get_course_by_doc_name,
                                           same["doc_name"])
            if twin:
                await asyncio.to_thread(
                    mark_file_processed, file_id, file_name, folder_id,
                    update_time, content_hash,
                )
                notify = (f"📄 «{file_name}» — то же содержимое, что у курса "
                          f"«{twin['doc_name']}» (№{twin['id']}): вопросы "
                          "общие, новый курс не создан.")
                for hr_id in _hr_ids():
                    await _send(str(hr_id), notify, HR_BOT_ID)
                print(f"[process_new_document] content twin of "
                      f"{same['doc_name']!r} — course skipped")
                return

        # 7. Generate questions
        from app.course_generator import generate_questions  # noqa: PLC0415

        questions = await asyncio.to_thread(generate_questions, file_name, new_chunks)
        questions_json = json.dumps(questions, ensure_ascii=False)

        # 8. Persist to DB
        course_id = await asyncio.to_thread(
            save_draft_course, file_name, file_id, questions_json, detail_url
        )
        await asyncio.to_thread(
            mark_file_processed, file_id, file_name, folder_id, update_time,
            content_hash,
        )

        # 8.5. 19.08: стандарт доступен для вопросов СРАЗУ (чанки уже в
        # индексе) — сотрудники ролей документа узнают, не дожидаясь
        # «Подтвердить» (тест придёт позже отдельным анонсом)
        asyncio.create_task(_broadcast_new_standard(
            file_name, detail_url, list(roles)))

        # 9. Save draft JSON
        courses_dir = os.path.join(data_dir, "courses")
        os.makedirs(courses_dir, exist_ok=True)
        draft_path = os.path.join(courses_dir, f"{file_id}_draft.json")
        with open(draft_path, "w", encoding="utf-8") as f:
            json.dump(questions, f, ensure_ascii=False, indent=2)

        # 10. Notify HR (дизайн 17.08: сводка-страховка перед необратимым —
        # сколько вопросов и сколько получателей уйдёт рассылка)
        hr_ids = _hr_ids()
        course_row = await asyncio.to_thread(get_course_by_id, course_id)
        n_recipients = len(await asyncio.to_thread(
            _course_recipients, course_row)) if course_row else 0
        notify_text = (
            f"📄 Новый документ: *{file_name}*\n"
            f"15 вопросов (5 базовых + 10 экзаменационных) · "
            f"{n_recipients} подходящих получателей рассылки.\n"
            "После подтверждения курс активируется и рассылка уйдёт сразу.\n\n"
            f"Посмотреть вопросы: Вопросы {course_id}\n"
            f"Активировать курс: Подтвердить {course_id}"
        )
        if parsed["suspicious"]:
            notify_text += (
                f"\n\n⚠️ Возможная опечатка в префиксе: «{parsed['suspicious']}» — "
                "такого департамента нет в реестре ролей (data/roles.json). "
                "Проверь имя файла."
            )
        notify_kb = (keyboards.hr_new_course_actions(course_id)
                     if BUTTONS_ENABLED else None)
        for hr_id in hr_ids:
            await _send(str(hr_id), notify_text, HR_BOT_ID,
                        **_kb_kwargs(notify_kb))
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


async def _commit_disk_file(dialog_id: str, disk_file_id) -> None:
    """Файл с Диска в чат: im.dialog.get → im.disk.file.commit (каскад №4).

    Методы im.disk.* без детальных секций в выжимке доков — каскад попыток;
    любой фейл тихо логируется (best-effort)."""
    try:
        dialog_id = bare_dialog(dialog_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                BITRIX_WEBHOOK_URL + "im.dialog.get", json={"DIALOG_ID": dialog_id}
            )
            r.raise_for_status()
            dialog = r.json().get("result") or {}
            chat_id = dialog.get("chat_id") or dialog.get("id") or dialog.get("ID")
            if not chat_id:
                print(f"[disk-commit] no chat_id for {dialog_id}: {dialog}")
                return
            for id_key in ("UPLOAD_ID", "DISK_ID"):
                r2 = await client.post(
                    BITRIX_WEBHOOK_URL + "im.disk.file.commit",
                    json={"CHAT_ID": chat_id, id_key: int(disk_file_id)},
                )
                if r2.status_code == 200 and r2.json().get("result"):
                    print(f"[disk-commit] sent file {disk_file_id} "
                          f"to {dialog_id} ({id_key})")
                    return
                print(f"[disk-commit] {id_key} attempt failed: "
                      f"{r2.status_code} {r2.text[:200]}")
    except Exception as exc:
        print(f"[disk-commit] failed for {dialog_id}: {exc!r}")


async def _send_course_file(dialog_id: str, course_id: int) -> None:
    """Приложить файл документа в чат при старте курса (№4): фейл тихий —
    ссылка на документ уже есть в тексте _start_reading."""
    course = await asyncio.to_thread(get_course_by_id, course_id)
    doc_id = (course or {}).get("doc_id")
    if doc_id:
        await _commit_disk_file(dialog_id, doc_id)


async def _build_and_send_report(dialog_id: str) -> None:
    """№10: xlsx-матрица → приватная папка Диска → в чат HR. Best-effort:
    любой фейл — лог, HR уже получил текстовую сводку."""
    try:
        rows = await asyncio.to_thread(get_report_rows)
        employees = {str(e["bitrix_uid"]): e
                     for e in await asyncio.to_thread(get_all_employees)}
        courses = await asyncio.to_thread(get_active_courses)
        data = await asyncio.to_thread(build_report_xlsx, rows, employees,
                                       courses)
        name = "Отчёт по обучению.xlsx"
        old_id = await asyncio.to_thread(get_meta, "last_report_file_id")
        async with httpx.AsyncClient(timeout=60.0) as client:
            if old_id:
                try:
                    await client.post(
                        BITRIX_WEBHOOK_URL + "disk.file.markdeleted",
                        json={"id": old_id})
                except Exception:
                    pass          # удалён руками / уже в корзине — не важно
            r = await client.post(
                BITRIX_WEBHOOK_URL + "disk.folder.uploadfile",
                json={"id": REPORTS_FOLDER_ID,
                      "data": {"NAME": name},
                      "fileContent": [name, base64.b64encode(data).decode()],
                      "generateUniqueName": True})
        file_id = (r.json().get("result") or {}).get("ID")
        if not file_id:
            print(f"[report-xlsx] upload failed: {r.status_code} {r.text[:200]}")
            return
        await asyncio.to_thread(set_meta, "last_report_file_id", str(file_id))
        await _commit_disk_file(dialog_id, file_id)
    except Exception as exc:
        import traceback
        print(f"[report-xlsx] ERROR: {exc}\n{traceback.format_exc()}")
