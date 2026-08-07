"""
app/db.py — SQLite persistence for onboarding state: courses, sessions, answers.

All functions are synchronous (sqlite3 stdlib). Call them from async FastAPI
handlers via: await asyncio.to_thread(fn, *args)
"""
import json
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "onboarding.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    """ALTER TABLE ADD COLUMN если колонки нет — CREATE IF NOT EXISTS существующую
    таблицу не мигрирует."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init_db() -> None:
    """Create tables if they don't exist. Safe to call multiple times."""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS courses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_name        TEXT NOT NULL,
                doc_id          TEXT,
                doc_detail_url  TEXT,
                questions_json  TEXT NOT NULL,
                created_at      TEXT DEFAULT (datetime('now')),
                approved_at     TEXT,
                approved_by     TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       TEXT NOT NULL,
                dialog_id     TEXT,
                course_id     INTEGER NOT NULL,
                state         TEXT NOT NULL DEFAULT 'READING',
                current_q_idx INTEGER DEFAULT 0,
                score_basic   INTEGER DEFAULT 0,
                score_exam    INTEGER DEFAULT 0,
                role          TEXT,
                started_at    TEXT DEFAULT (datetime('now')),
                updated_at    TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );

            CREATE TABLE IF NOT EXISTS answers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   INTEGER NOT NULL,
                question_id  INTEGER NOT NULL,
                phase        TEXT NOT NULL,
                user_answer  TEXT NOT NULL,
                is_correct   INTEGER,
                answered_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS processed_files (
                file_id      TEXT PRIMARY KEY,
                doc_name     TEXT,
                folder_id    TEXT,
                processed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS employees (
                bitrix_uid TEXT PRIMARY KEY,
                email      TEXT,
                full_name  TEXT,
                added_by   TEXT,
                added_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS seen_portal_users (
                bitrix_uid  TEXT PRIMARY KEY,
                departments TEXT,
                first_seen  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        _ensure_column(conn, "sessions", "role", "TEXT")
        # №4: архив курса при удалении файла; детект новой версии при том же file_id
        _ensure_column(conn, "courses", "archived_at", "TEXT")
        _ensure_column(conn, "processed_files", "update_time", "TEXT")
        # Демо-фидбек C: должность в отчётах (из user.get при «Пригласить»)
        _ensure_column(conn, "employees", "work_position", "TEXT")
        # Гейт поллера: файлы, обработанные ДО появления processed_files,
        # известны по courses.doc_id — сидируем, чтобы их не переобработать.
        conn.execute(
            """INSERT OR IGNORE INTO processed_files (file_id, doc_name, processed_at)
               SELECT doc_id, doc_name, created_at FROM courses WHERE doc_id IS NOT NULL"""
        )
        # Whitelist: юзеры с сессиями (до появления employees) остаются допущенными.
        conn.execute(
            """INSERT OR IGNORE INTO employees (bitrix_uid, added_by)
               SELECT DISTINCT user_id, 'seed' FROM sessions"""
        )
        # Тестировщики: HR из HR_USER_IDS автоматически допущены к employee-боту.
        # INSERT OR IGNORE = идемпотентно; «Пригласить» поверх сида дополняет
        # email/ФИО, не затирается.
        for uid in os.getenv("HR_USER_IDS", "").split(","):
            if uid.strip():
                conn.execute(
                    "INSERT OR IGNORE INTO employees (bitrix_uid, added_by)"
                    " VALUES (?, 'hr-seed')",
                    (uid.strip(),),
                )


# ── Courses ──────────────────────────────────────────────────────────────────

def save_draft_course(doc_name: str, doc_id: str, questions_json: str,
                       doc_detail_url: str = None) -> int:
    """Insert a new course draft. Returns the new course id."""
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO courses (doc_name, doc_id, doc_detail_url, questions_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (doc_name, doc_id, doc_detail_url, questions_json, now),
        )
        return cur.lastrowid


def activate_course(doc_id: str, hr_user_id: str) -> bool:
    """Mark course as approved by Bitrix file ID. Returns True if found."""
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        result = conn.execute(
            "UPDATE courses SET approved_at = ?, approved_by = ? WHERE doc_id = ?",
            (now, hr_user_id, doc_id),
        )
        return result.rowcount > 0


def activate_course_by_id(course_id: int, hr_user_id: str) -> bool:
    """Mark course as approved by DB course ID. Returns True if found."""
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        result = conn.execute(
            "UPDATE courses SET approved_at = ?, approved_by = ? WHERE id = ?",
            (now, hr_user_id, course_id),
        )
        return result.rowcount > 0


def get_pending_courses() -> list[dict]:
    """Courses not yet approved, oldest first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM courses WHERE approved_at IS NULL ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_course_by_doc_id(doc_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM courses WHERE doc_id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None


def get_course_by_id(course_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        return dict(row) if row else None


def get_course_by_doc_name(doc_name: str) -> dict | None:
    """Последний курс с таким именем документа — для дедупа копий из разных
    ролевых папок (у копий разные file_id, но одно имя)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM courses WHERE doc_name = ? ORDER BY id DESC LIMIT 1",
            (doc_name,),
        ).fetchone()
        return dict(row) if row else None


def get_active_courses() -> list[dict]:
    """All approved, non-archived courses, newest first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM courses WHERE approved_at IS NOT NULL"
            " AND archived_at IS NULL ORDER BY approved_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def set_course_archived(course_id: int, archived: bool) -> None:
    """Архив при удалении файла документа; разархив при повторной заливке."""
    value = datetime.utcnow().isoformat() if archived else None
    with _conn() as conn:
        conn.execute(
            "UPDATE courses SET archived_at = ? WHERE id = ?", (value, course_id)
        )


def get_course_questions(course_id: int) -> dict:
    """Return the parsed questions JSON for a course, or empty dict."""
    course = get_course_by_id(course_id)
    if not course:
        return {}
    return json.loads(course["questions_json"])


def update_course_questions(course_id: int, questions_json: str) -> bool:
    """Заменить questions_json курса (правка HR). approved_at НЕ трогаем —
    правка активного курса не должна его деактивировать."""
    with _conn() as conn:
        result = conn.execute(
            "UPDATE courses SET questions_json = ? WHERE id = ?",
            (questions_json, course_id),
        )
        return result.rowcount > 0


# ── Sessions ─────────────────────────────────────────────────────────────────

def get_session(user_id: str) -> dict | None:
    """Latest non-DONE session for a user, or None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? AND state != 'DONE'"
            " ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def get_session_by_id(session_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None


def create_session(user_id: str, dialog_id: str, course_id: int,
                   state: str = "READING") -> dict:
    """Create a new session (READING, либо ROLE_SELECT до выбора роли)."""
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO sessions
               (user_id, dialog_id, course_id, state, current_q_idx,
                score_basic, score_exam, started_at, updated_at)
               VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?)""",
            (user_id, dialog_id, course_id, state, now, now),
        )
        session_id = cur.lastrowid
    # with block has committed — safe to query in new connection
    return get_session_by_id(session_id)


def update_session(session_id: int, state: str = None, q_idx: int = None,
                    score_basic: int = None, score_exam: int = None,
                    role: str = None, course_id: int = None) -> None:
    """Update non-None fields on a session."""
    parts, vals = ["updated_at = ?"], [datetime.utcnow().isoformat()]
    if state is not None:
        parts.append("state = ?"); vals.append(state)
    if q_idx is not None:
        parts.append("current_q_idx = ?"); vals.append(q_idx)
    if role is not None:
        parts.append("role = ?"); vals.append(role)
    if course_id is not None:
        parts.append("course_id = ?"); vals.append(course_id)
    if score_basic is not None:
        parts.append("score_basic = ?"); vals.append(score_basic)
    if score_exam is not None:
        parts.append("score_exam = ?"); vals.append(score_exam)
    vals.append(session_id)
    with _conn() as conn:
        conn.execute(f"UPDATE sessions SET {', '.join(parts)} WHERE id = ?", vals)


def update_session_by_user(user_id: str, state: str, q_idx: int = 0) -> bool:
    """Update the active session for a user. Returns True if found."""
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE user_id = ? AND state != 'DONE'"
            " ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE sessions SET state = ?, current_q_idx = ?, updated_at = ? WHERE id = ?",
            (state, q_idx, now, row["id"]),
        )
        return True


def get_sessions_by_user(user_id: str) -> list[dict]:
    """ВСЕ сессии сотрудника, включая DONE, новые сверху (для «Истории»)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_report_rows() -> list[dict]:
    """Все сессии с именем курса и его вопросами (для «Отчёта»), свежие сверху."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT s.*, c.doc_name, c.questions_json FROM sessions s
               JOIN courses c ON c.id = s.course_id
               ORDER BY s.updated_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_session_dialog_id(user_id: str) -> str | None:
    """Return the dialog_id stored in the active session for a user."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT dialog_id FROM sessions WHERE user_id = ? AND state != 'DONE'"
            " ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["dialog_id"] if row else None


# ── Processed files (гейт поллера, независим от courses) ────────────────────

def is_file_processed(file_id: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_files WHERE file_id = ?", (str(file_id),)
        ).fetchone()
        return row is not None


def get_processed_file(file_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM processed_files WHERE file_id = ?", (str(file_id),)
        ).fetchone()
        return dict(row) if row else None


def mark_file_processed(file_id: str, doc_name: str, folder_id: str = None,
                        update_time: str = None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO processed_files
               (file_id, doc_name, folder_id, processed_at, update_time)
               VALUES (?, ?, ?, ?, ?)""",
            (str(file_id), doc_name, folder_id,
             datetime.utcnow().isoformat(), update_time),
        )


def remove_processed_file(file_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "DELETE FROM processed_files WHERE file_id = ?", (str(file_id),)
        )


def get_processed_by_folders(folder_ids: list[str]) -> list[dict]:
    """processed_files по папкам (синк удалений №4). Legacy-строки с NULL
    folder_id (сидинг из courses) сюда НЕ попадают — они не привязаны к папкам."""
    if not folder_ids:
        return []
    placeholders = ",".join("?" * len(folder_ids))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM processed_files WHERE folder_id IN ({placeholders})",
            [str(f) for f in folder_ids],
        ).fetchall()
        return [dict(r) for r in rows]


def count_processed_by_doc_name(doc_name: str) -> int:
    """Сколько живых копий документа осталось (копии в других ролевых папках)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM processed_files WHERE doc_name = ?", (doc_name,)
        ).fetchone()
        return row[0]


# ── Employees (whitelist доступа к обучению) ─────────────────────────────────

def add_employee(bitrix_uid: str, email: str = None, full_name: str = None,
                 added_by: str = None, work_position: str = None) -> None:
    """Добавить/обновить сотрудника в whitelist. Повторное приглашение обновляет
    email/ФИО/должность (в т.ч. у seed-записей), added_at первой записи сохраняется."""
    email = email.strip().lower() if email else None
    with _conn() as conn:
        conn.execute(
            """INSERT INTO employees (bitrix_uid, email, full_name, added_by,
                                      work_position, added_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(bitrix_uid) DO UPDATE SET
                 email = COALESCE(excluded.email, email),
                 full_name = COALESCE(excluded.full_name, full_name),
                 work_position = COALESCE(excluded.work_position, work_position)""",
            (str(bitrix_uid), email, full_name, added_by, work_position,
             datetime.utcnow().isoformat()),
        )


def is_employee_allowed(bitrix_uid: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM employees WHERE bitrix_uid = ?", (str(bitrix_uid),)
        ).fetchone()
        return row is not None


def get_employee_by_email(email: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None


def get_all_employees() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM employees").fetchall()
        return [dict(r) for r in rows]


def get_employee(bitrix_uid: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE bitrix_uid = ?", (str(bitrix_uid),)
        ).fetchone()
        return dict(row) if row else None


# ── Meta (служебные метки, key-value) ────────────────────────────────────────

def get_meta(key: str) -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )


# ── Seen portal users (память поллера отделов, НЕ whitelist) ─────────────────

def is_user_seen(bitrix_uid: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_portal_users WHERE bitrix_uid = ?", (str(bitrix_uid),)
        ).fetchone()
        return row is not None


def mark_user_seen(bitrix_uid: str, departments_json: str = None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO seen_portal_users (bitrix_uid, departments, first_seen)
               VALUES (?, ?, ?)""",
            (str(bitrix_uid), departments_json, datetime.utcnow().isoformat()),
        )


def get_user_departments(bitrix_uid: str) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT departments FROM seen_portal_users WHERE bitrix_uid = ?",
            (str(bitrix_uid),),
        ).fetchone()
        return row["departments"] if row else None


def update_user_departments(bitrix_uid: str, departments_json: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE seen_portal_users SET departments = ? WHERE bitrix_uid = ?",
            (departments_json, str(bitrix_uid)),
        )


def seen_users_empty() -> bool:
    """True до первого прогона поллера отделов — первый прогон тихий (без спама HR)."""
    with _conn() as conn:
        return conn.execute("SELECT 1 FROM seen_portal_users LIMIT 1").fetchone() is None


# ── Answers ──────────────────────────────────────────────────────────────────

def log_answer(session_id: int, question_id: int, phase: str,
               user_answer: str, is_correct: int | None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO answers
               (session_id, question_id, phase, user_answer, is_correct, answered_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, question_id, phase, user_answer, is_correct,
             datetime.utcnow().isoformat()),
        )


def get_session_answers(session_id: int, phase: str = None) -> list[dict]:
    """All answers for a session, optionally filtered by phase."""
    with _conn() as conn:
        if phase:
            rows = conn.execute(
                "SELECT * FROM answers WHERE session_id = ? AND phase = ?"
                " ORDER BY question_id",
                (session_id, phase),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM answers WHERE session_id = ? ORDER BY phase, question_id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
