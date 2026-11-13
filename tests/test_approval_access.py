import json

import numpy as np

from app import db, rag
from app.document_access import available_chunks, reconcile_sources
from app.state_machine import _my_documents_text


def draft(name="standard.docx", file_id="42"):
    return db.save_draft_course(name, file_id, json.dumps({}))


def test_draft_hidden_and_approval_is_version_specific():
    first = draft()
    second = draft(file_id="43")
    chunks = [{"course_id": first}, {"course_id": second}, {"doc_name": "unknown"}]
    assert available_chunks(chunks) == []
    assert db.activate_course_by_id(first, "hr")
    assert available_chunks(chunks) == chunks[:1]
    assert not db.activate_course_by_id(first, "other")
    assert db.get_course_by_id(first)["approved_by"] == "hr"
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM course_events WHERE event='announcement_queued'").fetchone()[0] == 1


def test_replacement_retains_old_until_approval_and_snapshot():
    first = draft()
    db.set_course_source(first, "folder/standard.docx")
    db.activate_course_by_id(first, "hr")
    second = draft(file_id="43")
    db.set_course_source(second, "folder/standard.docx")
    chunks = [{"course_id": first}, {"course_id": second}]
    assert available_chunks(chunks) == chunks[:1]
    db.activate_course_by_id(second, "hr")
    assert available_chunks(chunks) == chunks[1:]


def test_legacy_ambiguous_unknown_and_file_priority():
    first = draft()
    db.activate_course_by_id(first, "hr")
    db.mark_file_processed("42", "standard.docx", "7")
    records = reconcile_sources([{"file_id": "42"}, {"doc_name": "standard.docx", "folder_id": "7"},
                                 {"doc_name": "standard.docx"}])
    assert [r["available"] for r in records] == [True, True, False]
    draft(file_id="42")
    assert reconcile_sources([{"file_id": "42"}])[0]["status"] == "ambiguous"


def test_no_approved_context_does_not_call_openai(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("OpenAI must not be called")
    monkeypatch.setattr(rag, "OpenAI", forbidden)
    chunks = [{"course_id": draft(), "doc_name": "standard.docx", "roles": ["all_staff"]}]
    assert rag.answer("question", chunks, np.ones((1, 2)), None, None)[1] == []
    assert "standard" not in _my_documents_text(None, chunks)
