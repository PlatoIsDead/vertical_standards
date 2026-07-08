"""
app/gamification.py — геймификация (доработка №5): «Благодарность» в Ленту
новостей после сдачи экзамена + еженедельный рейтинг лучших по обучению.

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
        exam_total = len(questions.get("exam_questions", []))
        # Формула сдачи — та же, что в _finish_phase / hr_tools._session_status
        if not exam_total or r["score_exam"] < round(exam_total * 0.7):
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
                     f"(экзамен {r['score_exam']}/{exam_total})")

    message = ("За последнюю неделю обучение завершили лучше всех:\n\n"
               + "\n".join(lines) + "\n\nПоздравляем! 🎉")
    return RATING_TITLE, message


# ── Публикация в Ленту ───────────────────────────────────────────────────────

def _post_to_feed(title: str, message: str) -> bool:
    """log.blogpost.add с DEST=["UA"] (все сотрудники). Retry только на сетевых
    ошибках; отказ прав/параметров не ретраим — лог и False."""
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


def post_exam_congratulation(user_id: str, questions: dict,
                             exam_score: int, basic_score: int) -> None:
    """Хук из _finish_phase (только при passed). Любые ошибки глотаются —
    завершение экзамена не роняем."""
    try:
        emp = get_employee(str(user_id))
        fio = (emp or {}).get("full_name") or f"Сотрудник (ID {user_id})"
        title, message = build_congrats(
            fio,
            questions.get("doc_name") or "обучение",
            exam_score, len(questions.get("exam_questions", [])),
            basic_score, len(questions.get("basic_questions", [])),
        )
        _post_to_feed(title, message)
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
