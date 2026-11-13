import json

from app import db


def test_release_migration_archives_progress_and_keeps_completed():
    questions = json.dumps({"basic_questions": [], "exam_questions": []})
    cid = db.save_draft_course("course", "file", questions)
    active = db.create_session("u", "d", cid, state="EXAM_READY", quiz_version=1)
    done = db.create_session("v", "e", cid, state="DONE", quiz_version=1)
    db.update_session(active["id"], score_basic=2)
    with db._conn() as conn:
        conn.execute("INSERT INTO answers(session_id,question_id,phase,user_answer,is_correct) "
                     "VALUES(?,0,'basic','A',1)", (active["id"],))
    assert db.migrate_active_quizzes_to_unified() == 1
    migrated = db.get_session_by_id(active["id"])
    assert migrated["quiz_version"] == 2 and migrated["state"] == "READING"
    assert migrated["score_basic"] == 0 and migrated["attempt"] == 1
    assert migrated["quiz_questions_json"] == questions
    assert db.get_session_by_id(done["id"]) == done
    with db._conn() as conn:
        archive = conn.execute("SELECT * FROM legacy_quiz_archive").fetchone()
        assert json.loads(archive["session_json"])["score_basic"] == 2
        assert len(json.loads(archive["answers_json"])) == 1
        assert conn.execute("SELECT count(*) FROM answers").fetchone()[0] == 0
    assert db.migrate_active_quizzes_to_unified() == 0
