"""№8: авто-роль из отдела Битрикса (roles.json departments)."""
import json

import pytest

import app.db as db
import app.roles as roles
import app.state_machine as sm

QUESTIONS = {
    "course_summary": "s",
    "basic_questions": [
        {"id": 0, "text": "?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
         "correct": "A", "explanation": ""},
    ],
    "exam_questions": [],
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    db.add_employee("u1", added_by="test")

    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"housekeeper": "Горничная / Уборщица",
                  "admin_reception": "СПиР", "all_staff": "Все"},
        "prefixes": {"ALL": "all_staff"},
        "folders": {},
        "departments": {"1307": "housekeeper", "1308": "admin_reception"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))

    cid = db.save_draft_course("Стандарты.docx", "1",
                               json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid, "hr")
    monkeypatch.setattr(sm, "rag_answer", lambda **kw: ("MOCK", []))
    monkeypatch.setattr(sm, "notify_hr", lambda text: None)
    return cid


def test_role_for_departments_pure(env):
    assert roles.role_for_departments([1307]) == "housekeeper"
    assert roles.role_for_departments(["1307"]) == "housekeeper"     # int/str
    assert roles.role_for_departments([1307, 999]) == "housekeeper"  # чужой игнор
    assert roles.role_for_departments([1307, 1308]) is None          # разные роли
    assert roles.role_for_departments([]) is None


def test_auto_role_from_seen_departments(env):
    db.mark_user_seen("u1", "[1307]")            # поллер отделов уже видел
    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "по отделу" in reply and "Горничная" in reply
    session = db.get_session("u1")
    assert session["role"] == "housekeeper" and session["state"] == "READING"


def test_auto_role_beats_session_memory(env):
    """Перевод в другой отдел должен менять роль: отдел > память сессии."""
    std = db.get_course_by_doc_name("Стандарты.docx")["id"]
    s = db.create_session("u1", "d1", std, state="DONE")
    db.update_session(s["id"], role="admin_reception")   # память: СПиР
    cid2 = db.save_draft_course("Правила.docx", "2",
                                json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid2, "hr")
    db.mark_user_seen("u1", "[1307]")                    # отдел: горничная

    sm.process_message("u1", "привет", "d1", [], None)
    assert db.get_session("u1")["role"] == "housekeeper"


def test_unseen_user_live_lookup(env, monkeypatch):
    class _Resp:
        def json(self):
            return {"result": [{"ID": "u1", "UF_DEPARTMENT": [1307]}]}

    monkeypatch.setattr(sm.httpx, "post",
                        lambda url, json=None, timeout=None: _Resp())
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://x/")
    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "по отделу" in reply
    assert db.get_session("u1")["role"] == "housekeeper"


def test_network_failure_falls_back_to_menu(env, monkeypatch):
    def broken(url, json=None, timeout=None):
        raise sm.httpx.ConnectError("флап WSL")

    monkeypatch.setattr(sm.httpx, "post", broken)
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://x/")
    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "выбери свою роль" in reply
    assert db.get_session("u1")["state"] == "ROLE_SELECT"


def test_empty_departments_no_network(env, tmp_path, monkeypatch):
    cfg2 = tmp_path / "r2.json"
    cfg2.write_text(json.dumps({
        "roles": {"housekeeper": "Горничная", "all_staff": "Все"},
        "prefixes": {}, "folders": {}, "departments": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg2))

    def boom(*a, **kw):
        raise AssertionError("сеть не должна дёргаться при пустом маппинге")

    monkeypatch.setattr(sm.httpx, "post", boom)
    sm.process_message("u1", "привет", "d1", [], None)
    assert db.get_session("u1")["state"] == "ROLE_SELECT"   # меню как раньше
