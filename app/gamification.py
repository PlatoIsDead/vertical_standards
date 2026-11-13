"""
app/gamification.py — геймификация (доработка №5): штатная «Благодарность»
с медалью после сдачи экзамена + еженедельный рейтинг лучших по обучению.

Благодарность (16.09): log.blogpost.add с ВЕРХНЕУРОВНЕВЫМИ GRATITUDE_MEDAL и
GRATITUDE_EMPLOYEES (внутри UF_GRATITUDE Битрикс их игнорирует — отсюда
ошибочный вывод 31.08 «REST не умеет»). DEST = только сам сотрудник: медаль
видна всем в профиле, общая Лента не засоряется. Неизвестный код медали
Битрикс молча превращает в обычный пост — поэтому после add проверяем
UF_GRATITUDE и удаляем пост, если благодарность не создалась.

Чистые функции расписания/текстов принимают время параметром (тестируемость).
Два времени НЕ смешивать: расписание постинга — МСК (UTC+3, без DST),
окно «за 7 дней» — naive UTC (так пишет БД: datetime.utcnow().isoformat()).

Пост уходит через log.blogpost.add ОТ ИМЕНИ ВЛАДЕЛЬЦА вебхука (не бота) —
скоуп вебхука должен включать log. Любой фейл поста только логируется:
завершение экзамена ломать нельзя.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

from app import quiz
from app.db import (
    get_all_employees,
    get_employee,
    get_meta,
    get_report_rows,
    set_meta,
)

load_dotenv()

MSK = timezone(timedelta(hours=3))

RATING_TITLE = "🏆 Лучшие по обучению за неделю"
RATING_META_KEY = "last_rating_week"
_MEDALS = ("🥇", "🥈", "🥉")


# ── Расписание (чистое, tz-aware МСК) ────────────────────────────────────────

def week_key(now: datetime) -> str:
    """ISO-неделя: '2026-W28' (на стыке лет — ISO-год, не календарный)."""
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _monday_10(now: datetime) -> datetime:
    """Понедельник ISO-недели `now`, 10:00 в той же timezone."""
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=10, minute=0, second=0, microsecond=0)


def should_post_rating(now_msk: datetime, last_week: str | None) -> bool:
    """Пора ли постить рейтинг: пн ≥10:00 МСК и эта неделя ещё не публиковалась.
    Вторник/среда без публикации → True (догоняем пропущенный из-за рестарта пн)."""
    return week_key(now_msk) != (last_week or "") and now_msk >= _monday_10(now_msk)


# ── Тексты (чистые) ──────────────────────────────────────────────────────────

def build_congrats(fio: str, course_name: str, exam_score: int, exam_total: int,
                   basic_score: int, basic_total: int) -> tuple[str, str]:
    title = "Благодарность за обучение"
    message = (
        f"🏆 {fio} успешно сдал(а) экзамен по курсу «{course_name}»!\n"
        f"Результат: экзамен {exam_score}/{exam_total}, "
        f"базовый тест {basic_score}/{basic_total}.\n"
        "Поздравляем! 🎉"
    )
    return title, message


def build_weekly_rating(rows: list[dict], employees_by_uid: dict[str, dict],
                        now_utc: datetime) -> tuple[str, str] | None:
    """Топ-5 СДАВШИХ за последние 7 дней по сумме баллов. Только лучшие —
    никаких антирейтингов. None — за неделю никто не завершил (не постим)."""
    cutoff = (now_utc - timedelta(days=7)).isoformat()
    candidates = []
    for r in rows:
        if r.get("state") != "DONE" or (r.get("updated_at") or "") < cutoff:
            continue
        try:
            questions = json.loads(r.get("questions_json") or "{}")
        except json.JSONDecodeError:
            questions = {}
        _, exam_total, passed = quiz.result(r, questions)
        if not passed:
            continue
        candidates.append((r, exam_total))
    if not candidates:
        return None

    candidates.sort(key=lambda pair: (
        -(pair[0]["score_basic"] + pair[0]["score_exam"]),
        pair[0].get("updated_at") or "",
    ))

    lines, seen_users = [], set()
    for r, exam_total in candidates:
        uid = str(r["user_id"])
        if uid in seen_users:  # два курса за неделю — в рейтинге лучший результат
            continue
        seen_users.add(uid)
        if len(lines) == 5:
            break
        emp = employees_by_uid.get(uid)
        fio = (emp or {}).get("full_name") or f"Сотрудник (ID {uid})"
        badge = _MEDALS[len(lines)] if len(lines) < len(_MEDALS) else "•"
        score = r["score_basic"] + r["score_exam"]
        lines.append(f"{badge} {fio} — «{r['doc_name']}»: {score} баллов "
                     f"({'тест' if quiz.unified(r) else 'экзамен'} {r['score_exam']}/{exam_total})")

    message = ("За последнюю неделю обучение завершили лучше всех:\n\n"
               + "\n".join(lines) + "\n\nПоздравляем! 🎉")
    return RATING_TITLE, message


# ── Публикация в Ленту ───────────────────────────────────────────────────────

def _post_to_feed(title: str, message: str) -> bool:
    """log.blogpost.add с DEST=["UA"] (все сотрудники). Retry только на сетевых
    ошибках; отказ прав/параметров не ретраим — лог и False."""
    # Теперь только еженедельный рейтинг (на всех сотрудников). Выключен 31.08:
    # посты на всю компанию засоряют Ленту; благодарности за экзамен идут
    # через _post_gratitude адресно.
    if os.getenv("FEED_POSTS_ENABLED", "0") != "1":
        print(f"[gamification] feed posts disabled — пропущен пост {title!r}")
        return False
    webhook = os.getenv("BITRIX_WEBHOOK_URL", "")
    if not webhook:
        print("[gamification] BITRIX_WEBHOOK_URL missing — пост не отправлен")
        return False
    payload = {"POST_TITLE": title, "POST_MESSAGE": message, "DEST": ["UA"]}
    for attempt in range(1, 4):
        try:
            resp = httpx.post(webhook + "log.blogpost.add", json=payload, timeout=15.0)
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code == 200 and body.get("result"):
                print(f"[gamification] feed post ok: {title!r}")
                return True
            print(f"[gamification] feed post rejected: "
                  f"{resp.status_code} {resp.text[:200]}")
            return False
        except httpx.RequestError as exc:
            print(f"[gamification] feed post retry {attempt}/3: {exc!r}")
            if attempt < 3:
                time.sleep(2 * attempt)
    return False


def build_gratitude_payload(user_id: int, title: str, message: str,
                            medal: str) -> dict:
    """Благодарность видна в Ленте только получателю (и автору вебхука)."""
    return {
        "POST_TITLE": title,
        "POST_MESSAGE": message,
        "DEST": [f"U{user_id}"],
        "GRATITUDE_MEDAL": medal,
        "GRATITUDE_EMPLOYEES": [user_id],
    }


def _post_gratitude(user_id: str, title: str, message: str) -> bool:
    """Штатная благодарность с медалью. Retry только на сетевых ошибках add;
    пост без созданной благодарности удаляется (суррогаты не показываем)."""
    if os.getenv("GRATITUDE_ENABLED", "0") != "1":
        print(f"[gamification] gratitude disabled — пропущена для {user_id}")
        return False
    webhook = os.getenv("BITRIX_WEBHOOK_URL", "")
    if not webhook:
        print("[gamification] BITRIX_WEBHOOK_URL missing — благодарность не отправлена")
        return False
    uid = str(user_id).removeprefix("u")
    if not uid.isdigit():
        print(f"[gamification] gratitude: нечисловой user_id {user_id!r} — пропуск")
        return False
    medal = os.getenv("GRATITUDE_MEDAL", "cup")
    payload = build_gratitude_payload(int(uid), title, message, medal)

    post_id = None
    for attempt in range(1, 4):
        try:
            resp = httpx.post(webhook + "log.blogpost.add", json=payload, timeout=15.0)
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code == 200 and body.get("result"):
                post_id = body["result"]
                break
            print(f"[gamification] gratitude rejected: "
                  f"{resp.status_code} {resp.text[:200]}")
            return False
        except httpx.RequestError as exc:
            print(f"[gamification] gratitude retry {attempt}/3: {exc!r}")
            if attempt < 3:
                time.sleep(2 * attempt)
    if post_id is None:
        return False

    try:
        resp = httpx.post(webhook + "log.blogpost.get",
                          json={"POST_ID": post_id}, timeout=15.0)
        posts = resp.json().get("result") or []
        value = (posts[0].get("UF_GRATITUDE") or {}).get("VALUE") if posts else None
    except (httpx.RequestError, ValueError) as exc:
        # Пост создан, проверить не смогли — не удаляем вслепую
        print(f"[gamification] gratitude {post_id}: проверка не удалась {exc!r}")
        return True
    if value:
        print(f"[gamification] gratitude ok: post {post_id}, medal {medal!r}, user {uid}")
        return True

    print(f"[gamification] gratitude НЕ создана (медаль {medal!r}?) — "
          f"удаляю пост {post_id}")
    try:
        httpx.post(webhook + "log.blogpost.delete",
                   json={"POST_ID": post_id}, timeout=15.0)
    except httpx.RequestError as exc:
        print(f"[gamification] не удалось удалить пост {post_id}: {exc!r}")
    return False


def post_exam_congratulation(user_id: str, questions: dict,
                             exam_score: int, basic_score: int) -> None:
    """Хук из _finish_phase (только при passed). Любые ошибки глотаются —
    завершение экзамена не роняем."""
    try:
        emp = get_employee(str(user_id))
        fio = (emp or {}).get("full_name") or f"Сотрудник (ID {user_id})"
        if questions.get("quiz_version") == 2:
            title = "Благодарность за обучение"
            message = (f"🏆 {fio} успешно сдал(а) тест по курсу «{questions.get('doc_name', 'обучение')}»!\n"
                       f"Результат: {exam_score}/{len(quiz.all_questions(questions))}. Поздравляем! 🎉")
            _post_gratitude(str(user_id), title, message)
            return
        title, message = build_congrats(
            fio,
            questions.get("doc_name") or "обучение",
            exam_score, len(questions.get("exam_questions", [])),
            basic_score, len(questions.get("basic_questions", [])),
        )
        _post_gratitude(str(user_id), title, message)
    except Exception as exc:
        print(f"[gamification] congrats failed: {exc!r}")


def maybe_post_weekly_rating(now_msk: datetime = None) -> bool:
    """Ежечасно зовётся лупом. Идемпотентность через meta: неделя помечается
    обработанной ДАЖЕ если постить нечего (иначе попытки каждый час)."""
    now_msk = now_msk or datetime.now(MSK)
    if not should_post_rating(now_msk, get_meta(RATING_META_KEY)):
        return False
    rows = get_report_rows()
    employees = {e["bitrix_uid"]: e for e in get_all_employees()}
    result = build_weekly_rating(rows, employees, datetime.utcnow())
    set_meta(RATING_META_KEY, week_key(now_msk))
    if result is None:
        print("[gamification] рейтинг: за неделю никто не завершил — пропуск")
        return False
    return _post_to_feed(*result)
