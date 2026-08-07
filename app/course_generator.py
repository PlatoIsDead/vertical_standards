"""
app/course_generator.py — GPT-based question generation for staff onboarding courses.

Демо-фидбек D (05.08): генерация в ДВА этапа — сначала извлечь 15 ключевых
положений документа, потом составить по ним вопросы. Сигнатура
generate_questions и схема результата прежние (совместимость с questions_json,
правкой HR №3 и сквозной нумерацией 1–15).
"""
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

FACTS_PROMPT = """Ты методист учебных курсов для сотрудников апарт-отелей «Вертикаль».
Из фрагментов документа извлеки РОВНО 15 ключевых положений, которые сотрудник обязан знать.
Ответь строго в JSON формате:
{
  "course_summary": "краткое описание курса (2-3 предложения)",
  "facts_basic": ["...", "..."],
  "facts_exam": ["...", "..."]
}
Требования:
- facts_basic — ровно 5 простых фактов/правил (что, где, кто, сколько).
- facts_exam — ровно 10 положений посложнее: процессы, алгоритмы, исключения,
  границы ответственности.
- Каждое положение — одно конкретное проверяемое утверждение строго из документа,
  без общих слов. Язык: русский."""

QUESTIONS_FROM_FACTS_PROMPT = """Ты составляешь тест для сотрудников апарт-отелей «Вертикаль».
Для КАЖДОГО переданного положения составь один вопрос с 4 вариантами ответа.
Ответь строго в JSON формате:
{
  "basic_questions": [
    {
      "id": 0,
      "text": "текст вопроса",
      "options": ["A. вариант", "B. вариант", "C. вариант", "D. вариант"],
      "correct": "A",
      "explanation": "краткое объяснение правильного ответа со ссылкой на стандарт"
    }
  ],
  "exam_questions": [ { "та же структура" } ]
}
Требования:
- basic_questions — по одному вопросу на каждое из 5 положений facts_basic (итого ровно 5).
- exam_questions — по одному вопросу на каждое из 10 положений facts_exam (итого ровно 10).
- Ровно 4 варианта (A/B/C/D), correct — одна из букв A, B, C, D.
- Неверные варианты правдоподобны и из той же области (не очевидно абсурдные).
- Вопрос проверяет положение, а не цитирует его дословно. Язык: русский."""


def _llm_json(client: OpenAI, system: str, user: str, max_tokens: int,
              validate=None) -> dict:
    """JSON-вызов с 2 попытками (паттерн прежнего генератора): битый JSON или
    непрошедшая валидация → ретрай, после второй неудачи — ValueError."""
    for attempt in range(2):
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=max_tokens,
        )
        raw = response.choices[0].message.content
        try:
            result = json.loads(raw)
            if validate:
                validate(result)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[course_generator] attempt {attempt + 1} failed: {e}")
            if attempt == 1:
                raise ValueError(
                    f"LLM returned invalid JSON after 2 attempts: {e}") from e
    return {}  # unreachable


def generate_questions(doc_name: str, chunks: list[dict]) -> dict:
    """
    Двухэтапная генерация 5 базовых + 10 экзаменационных вопросов.

    Этап 1: 15 ключевых положений документа (+course_summary).
    Этап 2: вопрос с 4 вариантами на каждое положение.

    Returns:
        dict той же схемы, что раньше: doc_name, course_summary,
        basic_questions[5], exam_questions[10].

    Raises:
        ValueError: если LLM не вернул валидный JSON после ретраев любого этапа.
    """
    # timeout/retries — конвенция репо (rag.py): сеть WSL2 флапает, без таймаута
    # вызов может висеть; 60с — генерация тяжелее эмбеддингов
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                    timeout=60.0, max_retries=2)

    context = "\n\n---\n\n".join(
        f"[{c.get('heading', c.get('code', 'Раздел'))}]\n{c['text']}"
        for c in chunks[:15]
    )

    facts = _llm_json(
        client, FACTS_PROMPT,
        f"Документ: {doc_name}\n\n{context}",
        max_tokens=1200, validate=_validate_facts,
    )
    facts_json = json.dumps(
        {"facts_basic": facts["facts_basic"], "facts_exam": facts["facts_exam"]},
        ensure_ascii=False,
    )
    result = _llm_json(
        client, QUESTIONS_FROM_FACTS_PROMPT,
        f"Документ: {doc_name}\n\nПоложения:\n{facts_json}\n\n"
        f"Контекст документа (для правдоподобных неверных вариантов):\n{context}",
        max_tokens=3000, validate=_validate_questions,
    )
    result["doc_name"] = doc_name
    result["course_summary"] = facts.get("course_summary", "")
    return result


def _validate_facts(result: dict) -> None:
    """Raise ValueError if этап 1 вернул не 5+10 положений."""
    basic = result.get("facts_basic", [])
    exam = result.get("facts_exam", [])
    if len(basic) != 5:
        raise ValueError(f"Expected 5 facts_basic, got {len(basic)}")
    if len(exam) != 10:
        raise ValueError(f"Expected 10 facts_exam, got {len(exam)}")
    if not all(isinstance(f, str) and f.strip() for f in basic + exam):
        raise ValueError("facts must be non-empty strings")


def validate_question(q: dict) -> None:
    """Raise ValueError if a single question dict is invalid.

    Используется и генератором (все 15), и правкой HR (один вопрос, доработка №3)."""
    missing = [k for k in ("text", "options", "correct") if k not in q]
    if missing:
        raise ValueError(f"Question missing fields {missing}: {q}")
    if len(q["options"]) != 4:
        raise ValueError(f"Question must have 4 options, got {len(q['options'])}")
    if q["correct"] not in ("A", "B", "C", "D"):
        raise ValueError(f"correct must be A/B/C/D, got {q['correct']!r}")


def _validate_questions(result: dict) -> None:
    """Raise ValueError if the question structure is invalid."""
    basic = result.get("basic_questions", [])
    exam = result.get("exam_questions", [])

    if len(basic) != 5:
        raise ValueError(f"Expected 5 basic_questions, got {len(basic)}")
    if len(exam) != 10:
        raise ValueError(f"Expected 10 exam_questions, got {len(exam)}")

    for q in basic + exam:
        validate_question(q)
