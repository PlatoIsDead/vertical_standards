"""Демо-фидбек D: двухэтапная генерация (факты → вопросы), мок OpenAI-клиента."""
import json

import pytest

import app.course_generator as cg


def _q(i=0):
    return {"id": i, "text": f"Вопрос {i}?",
            "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
            "correct": "A", "explanation": "по стандарту"}


FACTS = {"course_summary": "О документе.",
         "facts_basic": [f"факт {i}" for i in range(5)],
         "facts_exam": [f"положение {i}" for i in range(10)]}
QUESTIONS = {"basic_questions": [_q(i) for i in range(5)],
             "exam_questions": [_q(i) for i in range(10)]}


class _Resp:
    def __init__(self, content):
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": msg})()]


class _FakeCompletions:
    def __init__(self, queue, calls):
        self._queue, self._calls = queue, calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        return _Resp(self._queue.pop(0))


def _fake_openai(monkeypatch, queue):
    calls = []

    class FakeClient:
        def __init__(self, *a, **kw):
            self.chat = type("Chat", (), {})()
            self.chat.completions = _FakeCompletions(queue, calls)

    monkeypatch.setattr(cg, "OpenAI", FakeClient)
    return calls


CHUNKS = [{"heading": "Раздел", "text": "текст стандарта"}]


def test_two_stage_success(monkeypatch):
    calls = _fake_openai(monkeypatch, [json.dumps(FACTS), json.dumps(QUESTIONS)])
    result = cg.generate_questions("RES Брони.docx", CHUNKS)

    assert len(calls) == 2
    assert "facts_basic" in calls[0]["messages"][0]["content"]   # этап 1: факты
    assert "Положения:" in calls[1]["messages"][1]["content"]    # этап 2 видит факты
    assert result["doc_name"] == "RES Брони.docx"
    assert result["course_summary"] == "О документе."
    assert len(result["basic_questions"]) == 5
    assert len(result["exam_questions"]) == 10
    cg._validate_questions(result)                               # схема прежняя


def test_retry_on_bad_json(monkeypatch):
    calls = _fake_openai(monkeypatch,
                         ["не json", json.dumps(FACTS), json.dumps(QUESTIONS)])
    result = cg.generate_questions("Док.docx", CHUNKS)
    assert len(calls) == 3                                       # 1 ретрай этапа 1
    assert len(result["basic_questions"]) == 5


def test_fails_after_two_bad_attempts(monkeypatch):
    _fake_openai(monkeypatch, ["мусор", "снова мусор"])
    with pytest.raises(ValueError):
        cg.generate_questions("Док.docx", CHUNKS)


def test_wrong_fact_count_retries(monkeypatch):
    bad_facts = dict(FACTS, facts_basic=["один факт"])           # 1 вместо 5
    calls = _fake_openai(monkeypatch, [json.dumps(bad_facts), json.dumps(FACTS),
                                       json.dumps(QUESTIONS)])
    result = cg.generate_questions("Док.docx", CHUNKS)
    assert len(calls) == 3
    assert result["course_summary"] == "О документе."
