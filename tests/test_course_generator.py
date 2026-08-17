"""Генерация вопросов: два этапа (факты → вопросы) + критик 17.08, shuffle
правильной буквы кодом, гейты качества — мок OpenAI-клиента очередью."""
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


def _verdicts(bad=(), reason="мета-вопрос"):
    return {"verdicts": [{"num": i, "ok": i not in bad,
                          "reason": reason if i in bad else ""}
                         for i in range(1, 16)]}


VERDICTS_OK = _verdicts()


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


def test_three_stage_success(monkeypatch):
    calls = _fake_openai(monkeypatch, [json.dumps(FACTS), json.dumps(QUESTIONS),
                                       json.dumps(VERDICTS_OK)])
    result = cg.generate_questions("RES Брони.docx", CHUNKS)

    assert len(calls) == 3
    assert "facts_basic" in calls[0]["messages"][0]["content"]   # этап 1: факты
    assert "Положения:" in calls[1]["messages"][1]["content"]    # этап 2 видит факты
    assert "verdicts" in calls[2]["messages"][0]["content"]      # этап 3: критик
    # совместимость с gpt-5.5: без temperature, max_completion_tokens
    assert "max_completion_tokens" in calls[0]
    assert "temperature" not in calls[0]
    assert result["doc_name"] == "RES Брони.docx"
    assert result["course_summary"] == "О документе."
    assert len(result["basic_questions"]) == 5
    assert len(result["exam_questions"]) == 10
    # 17.08: facts сохраняются (топливо «Перегенерировать N.q»)
    assert result["facts_basic"] == FACTS["facts_basic"]
    assert result["facts_exam"] == FACTS["facts_exam"]
    cg._validate_questions(result)                               # схема прежняя


def test_retry_on_bad_json(monkeypatch):
    calls = _fake_openai(monkeypatch,
                         ["не json", json.dumps(FACTS), json.dumps(QUESTIONS),
                          json.dumps(VERDICTS_OK)])
    result = cg.generate_questions("Док.docx", CHUNKS)
    assert len(calls) == 4                                       # 1 ретрай этапа 1
    assert len(result["basic_questions"]) == 5


def test_fails_after_two_bad_attempts(monkeypatch):
    _fake_openai(monkeypatch, ["мусор", "снова мусор"])
    with pytest.raises(ValueError):
        cg.generate_questions("Док.docx", CHUNKS)


def test_wrong_fact_count_retries(monkeypatch):
    bad_facts = dict(FACTS, facts_basic=["один факт"])           # 1 вместо 5
    calls = _fake_openai(monkeypatch, [json.dumps(bad_facts), json.dumps(FACTS),
                                       json.dumps(QUESTIONS),
                                       json.dumps(VERDICTS_OK)])
    result = cg.generate_questions("Док.docx", CHUNKS)
    assert len(calls) == 4
    assert result["course_summary"] == "О документе."


# ── 17.08: shuffle, гейты, критик ────────────────────────────────────────────

def test_shuffle_preserves_correct_body():
    src = {"text": "т?", "options": ["A. верный", "B. б", "C. в", "D. г"],
           "correct": "A"}
    letters = set()
    for _ in range(30):
        q = cg._shuffle_options(json.loads(json.dumps(src)))
        body = next(o for o in q["options"]
                    if o.startswith(q["correct"] + ". "))
        assert body == f"{q['correct']}. верный"                 # верный текст жив
        assert sorted(o.split(". ", 1)[1] for o in q["options"]) == \
            ["б", "в", "верный", "г"]
        letters.add(q["correct"])
    assert len(letters) > 1                                      # буквы гуляют


def test_shuffle_skips_broken_options():
    q = {"text": "т?", "options": ["просто текст", "B. б", "C. в", "D. г"],
         "correct": "A"}
    assert cg._shuffle_options(dict(q))["options"] == q["options"]


def test_validate_question_gates():
    good = _q()
    cg.validate_question(good)
    with pytest.raises(ValueError):                              # дубли вариантов
        cg.validate_question(dict(good, options=["A. 1", "B. 1", "C. 3", "D. 4"]))
    with pytest.raises(ValueError):                              # пустой текст
        cg.validate_question(dict(good, text="  "))
    with pytest.raises(ValueError):                              # пустое тело
        cg.validate_question(dict(good, options=["A. ", "B. 2", "C. 3", "D. 4"]))


def test_critic_repair_replaces_bad_question(monkeypatch):
    new_q = {"text": "Починенный?", "options": ["A. х", "B. у", "C. z", "D. w"],
             "correct": "A", "explanation": ""}
    calls = _fake_openai(monkeypatch, [
        json.dumps(FACTS), json.dumps(QUESTIONS),
        json.dumps(_verdicts(bad={2})), json.dumps(new_q),
    ])
    result = cg.generate_questions("Док.docx", CHUNKS)
    assert len(calls) == 4
    fixed = result["basic_questions"][1]                         # №2 = basic[1]
    assert fixed["text"] == "Починенный?"
    assert fixed["id"] == 1                                      # id прежний
    assert "забракован" in calls[3]["messages"][1]["content"]
    assert "факт 1" in calls[3]["messages"][1]["content"]        # его положение


def test_critic_failure_is_soft(monkeypatch):
    calls = _fake_openai(monkeypatch, [
        json.dumps(FACTS), json.dumps(QUESTIONS), "мусор", "снова мусор",
    ])
    result = cg.generate_questions("Док.docx", CHUNKS)           # не падает
    assert len(calls) == 4
    assert len(result["basic_questions"]) == 5


def test_context_not_sliced(monkeypatch):
    chunks = [{"heading": f"H{i}", "text": f"текст{i}"} for i in range(30)]
    calls = _fake_openai(monkeypatch, [json.dumps(FACTS), json.dumps(QUESTIONS),
                                       json.dumps(VERDICTS_OK)])
    cg.generate_questions("Док.docx", chunks)
    assert "текст29" in calls[0]["messages"][1]["content"]       # срез [:15] снят


def test_regenerate_one(monkeypatch):
    new_q = {"text": "Новый?", "options": ["A. х", "B. у", "C. z", "D. w"],
             "correct": "A", "explanation": ""}
    calls = _fake_openai(monkeypatch, [json.dumps(new_q)])
    q = cg.regenerate_one("Док.docx", CHUNKS, "важное положение", ["Старый?"])
    assert q["text"] == "Новый?"
    user = calls[0]["messages"][1]["content"]
    assert "важное положение" in user and "Старый?" in user
