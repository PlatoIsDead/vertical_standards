"""
app/deadlines.py — чистые функции №9 v2 (протокол клиента 05-07.08): назначения
курсов с дедлайнами, ежедневные напоминания сотруднику, двухступенчатая
эскалация руководителям.

Ни сети, ни БД. «Назначенность» — ЕДИНСТВЕННАЯ логика репо: роль ∩ роли курса
(без роли → только ALL), как в _course_recipients/pick_course_for_role.
Сдача — ЕДИНСТВЕННАЯ формула: score_exam >= round(exam_total * 0.7).
Времена — naive UTC ISO-строки, как пишет БД.

Дедлайн назначения = max(approved_at курса, added_at сотрудника, baseline) + N дней;
baseline — meta-метка первого запуска после деплоя (старые курсы не «просрочены»
пачкой в день деплоя).
"""
import json
from datetime import datetime, timedelta

from app.hr_tools import _session_status
from app.roles import ALL_STAFF, display_name
from app.state_machine import course_roles


def _passed(session: dict, questions: dict) -> bool:
    exam_total = len(questions.get("exam_questions", []))
    return bool(exam_total) and session["score_exam"] >= round(exam_total * 0.7)


def _safe_questions(course: dict) -> dict:
    try:
        return json.loads(course.get("questions_json") or "{}")
    except json.JSONDecodeError:
        return {}


def assignments_with_deadlines(employees: list[dict], courses: list[dict],
                               sessions_by_uid: dict, roles_by_uid: dict,
                               baseline: str, now: datetime,
                               days: int = 7) -> list[dict]:
    """Незакрытые назначения (все, кроме СДАННЫХ): провал не закрывает
    назначение — открывает пересдачу.

    Возвращает [{uid, course, deadline (datetime), days_left (по датам),
    status: not_started|in_progress|failed, session|None}].
    courses — get_active_courses() (архивные уже исключены).
    """
    out = []
    for e in employees:
        uid = str(e["bitrix_uid"])
        role = roles_by_uid.get(uid)
        sessions = sessions_by_uid.get(uid, [])
        latest_by_course: dict[int, dict] = {}
        for s in sessions:
            cid = s["course_id"]
            if cid not in latest_by_course or s["id"] > latest_by_course[cid]["id"]:
                latest_by_course[cid] = s
        for c in courses:
            croles = set(course_roles(c))
            assigned = ((role and ({role, ALL_STAFF} & croles))
                        or (role is None and ALL_STAFF in croles))
            if not assigned:
                continue
            questions = _safe_questions(c)
            latest = latest_by_course.get(c["id"])
            if latest and latest["state"] == "DONE":
                if not questions.get("exam_questions"):
                    continue                      # судить нечем — пропуск
                if _passed(latest, questions):
                    continue                      # сдан — назначение закрыто
                status = "failed"
            elif latest:
                status = "in_progress"
            else:
                status = "not_started"
            base = max(c.get("approved_at") or "", e.get("added_at") or "",
                       baseline or "")
            if not base:
                continue                          # нет ни одной даты — не судим
            deadline = datetime.fromisoformat(base) + timedelta(days=days)
            out.append({
                "uid": uid, "course": c, "deadline": deadline,
                "days_left": (deadline.date() - now.date()).days,
                "status": status, "session": latest,
            })
    return out


def _term(days_left: int) -> str:
    if days_left > 1:
        return f"осталось {days_left} дн."
    if days_left == 1:
        return "остался 1 день"
    if days_left == 0:
        return "сегодня — последний день"
    return f"просрочен на {-days_left} дн."


def build_reminder_text(items: list[dict]) -> str | None:
    """Ежедневное напоминание одному сотруднику: курсы + остаток дней."""
    if not items:
        return None
    lines = ["📅 Твои курсы и сроки:"]
    for it in sorted(items, key=lambda x: x["days_left"]):
        name = display_name(it["course"]["doc_name"])
        line = f"• «{name}» — {_term(it['days_left'])}"
        if it["status"] == "failed":
            line += " (экзамен не сдан — напиши *Пересдать*)"
        elif it["status"] == "not_started":
            line += " (напиши мне любое сообщение, чтобы начать)"
        lines.append(line)
    return "\n".join(lines)


def find_due_escalations(items: list[dict], already: set, stage: int,
                         now: datetime, days: int = 7) -> list[dict]:
    """Ступень 1: дедлайн истёк; ступень 2: истёк больше чем на days дней."""
    threshold = timedelta(days=(stage - 1) * days)
    return sorted(
        (it for it in items
         if (it["uid"], it["course"]["id"]) not in already
         and now > it["deadline"] + threshold),
        key=lambda it: it["deadline"],
    )


def build_escalation_message(due: list[dict], employees_by_uid: dict,
                             stage: int, days: int = 7) -> str | None:
    if not due:
        return None
    overdue_days = stage * days
    lines = [f"⚠️ Эскалация (ступень {stage}): обучение не пройдено "
             f"за {overdue_days} дн.:", ""]
    has_failed = False
    for it in due:
        emp = employees_by_uid.get(it["uid"])
        label = (emp.get("full_name") or f"ID {it['uid']}") if emp \
            else f"ID {it['uid']}"
        if emp and emp.get("work_position"):
            label += f", {emp['work_position']}"
        name = display_name(it["course"]["doc_name"])
        if it["session"] is None:
            status = "не начинал обучение"
        else:
            status = _session_status(it["session"],
                                     _safe_questions(it["course"]))
        if it["status"] == "failed":
            has_failed = True
        lines.append(f"• {label} — «{name}»: {status}, "
                     f"дедлайн был {it['deadline'].date().isoformat()}")
    if has_failed:
        lines += ["", "Сотрудник может пересдать экзамен командой «Пересдать»."]
    return "\n".join(lines)
