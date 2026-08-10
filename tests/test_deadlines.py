"""№9 v2: чистые функции дедлайнов — назначения, напоминания, две ступени
эскалации. Без сети и БД (роль-конфиг — tmp roles.json)."""
import json
from datetime import datetime, timedelta

import pytest

import app.deadlines as dl
import app.roles as roles

NOW = datetime(2026, 8, 20, 12, 0, 0)
OLD = (NOW - timedelta(days=10)).isoformat()      # дедлайн (7д) истёк 3 дня назад
VERY_OLD = (NOW - timedelta(days=20)).isoformat() # истёк 13 дней назад (ступень 2)
FRESH = (NOW - timedelta(days=2)).isoformat()

Q = json.dumps({"exam_questions": [
    {"id": i, "text": "?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
     "correct": "A"} for i in range(10)]})        # порог round(7.0)=7


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "roles.json"
    p.write_text(json.dumps({
        "roles": {"reservations": "Отдел бронирования",
                  "housekeeper": "Горничные", "all_staff": "Все"},
        "prefixes": {"RES": "reservations", "HSKP": "housekeeper",
                     "ALL": "all_staff"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(p))


def _course(cid, name, approved=OLD):
    return {"id": cid, "doc_name": name, "approved_at": approved,
            "questions_json": Q}


def _emp(uid, added=OLD):
    return {"bitrix_uid": uid, "added_at": added, "full_name": f"Емп {uid}",
            "work_position": None}


def _items(employees, courses, sessions=None, roles_map=None,
           baseline=OLD, now=NOW):
    return dl.assignments_with_deadlines(
        employees, courses, sessions or {}, roles_map or {}, baseline, now)


def test_assignment_by_role_and_all():
    courses = [_course(1, "RES Брони.docx"), _course(2, "ALL Общий.docx"),
               _course(3, "HSKP Уборка.docx")]
    items = _items([_emp("10")], courses, roles_map={"10": "reservations"})
    assert {it["course"]["id"] for it in items} == {1, 2}   # HSKP — чужой


def test_unknown_role_gets_only_all():
    courses = [_course(1, "RES Брони.docx"), _course(2, "ALL Общий.docx")]
    items = _items([_emp("10")], courses, roles_map={})
    assert [it["course"]["id"] for it in items] == [2]


def test_passed_excluded_failed_kept():
    courses = [_course(1, "ALL Общий.docx")]
    passed = {"10": [{"id": 1, "course_id": 1, "state": "DONE", "score_exam": 8}]}
    failed = {"10": [{"id": 1, "course_id": 1, "state": "DONE", "score_exam": 2}]}
    assert _items([_emp("10")], courses, passed) == []
    items = _items([_emp("10")], courses, failed)
    assert items[0]["status"] == "failed"


def test_latest_session_of_pair_wins():
    courses = [_course(1, "ALL Общий.docx")]
    sessions = {"10": [
        {"id": 1, "course_id": 1, "state": "DONE", "score_exam": 2},   # провал
        {"id": 5, "course_id": 1, "state": "DONE", "score_exam": 9},   # пересдал
    ]}
    assert _items([_emp("10")], courses, sessions) == []


def test_baseline_pushes_deadline():
    """Курс активирован давно, но baseline свежий → срок ещё не истёк."""
    items = _items([_emp("10", added=VERY_OLD)],
                   [_course(1, "ALL Общий.docx", approved=VERY_OLD)],
                   baseline=FRESH)
    assert items[0]["days_left"] > 0


def test_reminder_text_terms():
    courses = [_course(1, "ALL Общий.docx", approved=FRESH)]
    items = _items([_emp("10", added=FRESH)], courses, baseline=FRESH)
    text = dl.build_reminder_text(items)
    assert "«Общий»" in text and "осталось 5 дн." in text
    assert "чтобы начать" in text                           # not_started-подсказка

    overdue = _items([_emp("10")], [_course(1, "ALL Общий.docx")])
    assert "просрочен на 3 дн." in dl.build_reminder_text(overdue)
    assert dl.build_reminder_text([]) is None


def test_reminder_failed_offers_retake():
    sessions = {"10": [{"id": 1, "course_id": 1, "state": "DONE",
                        "score_exam": 2}]}
    items = _items([_emp("10")], [_course(1, "ALL Общий.docx")], sessions)
    assert "Пересдать" in dl.build_reminder_text(items)


def test_escalation_stages_and_already():
    items = _items([_emp("10", added=VERY_OLD)],
                   [_course(1, "ALL Общий.docx", approved=VERY_OLD)],
                   baseline=VERY_OLD)
    due1 = dl.find_due_escalations(items, set(), 1, NOW)
    due2 = dl.find_due_escalations(items, set(), 2, NOW)
    assert len(due1) == 1 and len(due2) == 1                # 13 дней — обе ступени
    assert dl.find_due_escalations(items, {("10", 1)}, 1, NOW) == []

    fresh_overdue = _items([_emp("10")], [_course(1, "ALL Общий.docx")])
    assert len(dl.find_due_escalations(fresh_overdue, set(), 1, NOW)) == 1
    assert dl.find_due_escalations(fresh_overdue, set(), 2, NOW) == []  # 3 дня < 14


def test_escalation_message():
    items = _items([_emp("10")], [_course(1, "ALL Общий.docx")])
    emps = {"10": {"full_name": "Иван Иванов", "work_position": "Администратор"}}
    text = dl.build_escalation_message(
        dl.find_due_escalations(items, set(), 1, NOW), emps, 1)
    assert "ступень 1" in text and "Иван Иванов, Администратор" in text
    assert "не начинал обучение" in text
    assert dl.build_escalation_message([], emps, 1) is None
