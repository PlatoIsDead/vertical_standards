"""db.py — миграция существующей БД и гейт processed_files."""
import sqlite3

import app.db as db


def _old_schema_db(path: str) -> None:
    """БД со старой схемой (без sessions.role, без processed_files)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_name TEXT NOT NULL, doc_id TEXT, doc_detail_url TEXT,
            questions_json TEXT NOT NULL,
            created_at TEXT, approved_at TEXT, approved_by TEXT
        );
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL, dialog_id TEXT, course_id INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'READING', current_q_idx INTEGER DEFAULT 0,
            score_basic INTEGER DEFAULT 0, score_exam INTEGER DEFAULT 0,
            started_at TEXT, updated_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO courses (doc_name, doc_id, questions_json) VALUES (?, ?, ?)",
        ("Старый документ.docx", "42", "{}"),
    )
    conn.commit()
    conn.close()


def test_init_db_migrates_old_schema(tmp_path, monkeypatch):
    db_file = str(tmp_path / "old.db")
    _old_schema_db(db_file)
    monkeypatch.setattr(db, "DB_PATH", db_file)

    db.init_db()

    conn = sqlite3.connect(db_file)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert "role" in cols
    # processed_files создан и засеян из courses.doc_id
    assert db.is_file_processed("42")
    conn.close()


def test_processed_files_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    db.init_db()

    assert not db.is_file_processed("100")
    db.mark_file_processed("100", "Документ.docx", folder_id="7")
    assert db.is_file_processed("100")


def test_get_course_by_doc_name(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    db.init_db()

    assert db.get_course_by_doc_name("Стандарт.docx") is None
    first = db.save_draft_course("Стандарт.docx", "1", "{}")
    second = db.save_draft_course("Стандарт.docx", "2", "{}")
    found = db.get_course_by_doc_name("Стандарт.docx")
    assert found["id"] == second and first != second


def test_employees_seeded_from_sessions(tmp_path, monkeypatch):
    """Legacy-юзеры с сессиями не должны отвалиться после появления whitelist."""
    db_file = str(tmp_path / "old.db")
    _old_schema_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO sessions (user_id, dialog_id, course_id) VALUES ('77', 'd77', 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", db_file)

    db.init_db()

    assert db.is_employee_allowed("77")
    assert not db.is_employee_allowed("999")


def test_add_employee_upsert(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    db.init_db()

    db.add_employee("50", added_by="seed")               # seed-запись без email
    db.add_employee("50", "Ivan@X.RU", "Иван Иванов", "9")  # «Пригласить» дополняет
    emp = db.get_employee_by_email("ivan@x.ru")          # email нормализован в lower
    assert emp and emp["bitrix_uid"] == "50"
    assert emp["full_name"] == "Иван Иванов"

    db.add_employee("50", added_by="9")                  # повтор без email — не затирает
    assert db.get_employee_by_email("ivan@x.ru")["bitrix_uid"] == "50"


def test_seen_users_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    db.init_db()

    assert db.seen_users_empty()
    assert not db.is_user_seen("300")
    db.mark_user_seen("300", "[1, 2]")
    assert db.is_user_seen("300")
    assert not db.seen_users_empty()
    assert db.get_user_departments("300") == "[1, 2]"
    db.update_user_departments("300", "[3]")
    assert db.get_user_departments("300") == "[3]"


def test_processed_by_folders_excludes_legacy_null(tmp_path, monkeypatch):
    """Legacy-строки (сидинг из courses, folder_id NULL) не должны попадать в синк
    удалений №4 — иначе первый прогон посчитает старые документы «удалёнными»."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    db.init_db()

    db.mark_file_processed("1", "Старый.docx")                    # legacy: folder NULL
    db.mark_file_processed("2", "Новый.docx", "111", "T1")
    rows = db.get_processed_by_folders(["111"])
    assert [r["file_id"] for r in rows] == ["2"]
    assert rows[0]["update_time"] == "T1"
    assert db.get_processed_by_folders([]) == []
    assert db.count_processed_by_doc_name("Старый.docx") == 1


def test_archived_course_excluded_from_active(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    db.init_db()

    course_id = db.save_draft_course("Док.docx", "1", "{}")
    db.activate_course_by_id(course_id, "hr")
    assert len(db.get_active_courses()) == 1
    db.set_course_archived(course_id, True)
    assert db.get_active_courses() == []
    db.set_course_archived(course_id, False)
    assert len(db.get_active_courses()) == 1


def test_update_session_role(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    db.init_db()

    course_id = db.save_draft_course("Док.docx", "1", "{}")
    session = db.create_session("u1", "d1", course_id, state="ROLE_SELECT")
    assert session["state"] == "ROLE_SELECT"
    assert session["role"] is None

    db.update_session(session["id"], state="READING", role="housekeeper")
    updated = db.get_session("u1")
    assert updated["state"] == "READING"
    assert updated["role"] == "housekeeper"


# ── №11: сид тестировщиков из HR_USER_IDS + course_id в update_session ───────

def test_hr_user_ids_seeded_as_employees(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("HR_USER_IDS", "28528, 36523")
    db.init_db()

    assert db.is_employee_allowed("28528")
    assert db.is_employee_allowed("36523")
    db.init_db()                                          # идемпотентно
    # «Пригласить» поверх сида дополняет данные, added_at не задваивается
    db.add_employee("36523", "d@x.ru", "Дмитрий", "9")
    assert db.get_employee("36523")["email"] == "d@x.ru"


def test_hr_seed_empty_env_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    assert db.get_all_employees() == []


# ── №9 v2: baseline, реестр руководителей, эскалации ─────────────────────────

def test_deadlines_baseline_set_once(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    first = db.get_meta("deadlines_baseline")
    assert first
    db.init_db()
    assert db.get_meta("deadlines_baseline") == first        # не перезаписан


def test_managers_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    assert db.get_managers()[0]["email"] == "n.sharapov@proptech.digital"  # сид
    assert db.add_manager("Chief@X.ru", "9", level=2)         # нормализация
    assert not db.add_manager("chief@x.ru", "9")              # дубль
    assert db.get_managers()[-1]["level"] == 2
    assert db.remove_manager("chief@x.ru")
    assert not db.remove_manager("chief@x.ru")


def test_escalations_marked_per_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    db.mark_escalated("10", 1, 1)
    db.mark_escalated("10", 1, 1)                             # идемпотентно
    assert db.get_escalated(1) == {("10", 1)}
    assert db.get_escalated(2) == set()


def test_last_done_session_skips_marker(tmp_path, monkeypatch):
    """Маркеры «нет курсов» (course_id=0) не считаются пересдаваемыми."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    cid = db.save_draft_course("Док.docx", "1", "{}")
    db.create_session("u1", "d1", cid, state="DONE")
    db.create_session("u1", "d1", 0, state="DONE")            # маркер позже
    assert db.get_last_done_session("u1")["course_id"] == cid


def test_update_session_course_id(tmp_path, monkeypatch):
    """Сентинел 0 на этапе ROLE_SELECT → реальный курс после выбора роли."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()

    cid = db.save_draft_course("Док.docx", "1", "{}")
    session = db.create_session("u1", "d1", 0, state="ROLE_SELECT")
    assert session["course_id"] == 0

    db.update_session(session["id"], state="READING", course_id=cid)
    updated = db.get_session("u1")
    assert updated["course_id"] == cid and updated["state"] == "READING"


# ── Демо-фидбек C: должность сотрудника ──────────────────────────────────────

def test_work_position_upsert(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()

    db.add_employee("50", "i@x.ru", "Иван", "9", work_position="Администратор")
    assert db.get_employee("50")["work_position"] == "Администратор"
    db.add_employee("50", added_by="9")               # повтор без должности
    assert db.get_employee("50")["work_position"] == "Администратор"  # не затёрта


def test_work_position_column_added_to_existing_table(tmp_path, monkeypatch):
    """Живая БД со старой employees-схемой получает колонку миграцией."""
    db_file = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE employees (bitrix_uid TEXT PRIMARY KEY, email TEXT,"
        " full_name TEXT, added_by TEXT, added_at TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setenv("HR_USER_IDS", "")

    db.init_db()

    db.add_employee("1", work_position="Техник")
    assert db.get_employee("1")["work_position"] == "Техник"
