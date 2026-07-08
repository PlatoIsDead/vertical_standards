"""
app/course_generator.py — GPT-based question generation for staff onboarding courses
"""
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

QUESTION_GEN_SYSTEM_PROMPT = """Ты создаёшь учебный курс для сотрудников апарт-отелей «Вертикаль».
Создай 5 базовых и 10 экзаменационных вопросов на основе предоставленных фрагментов документа.
Ответь строго в JSON формате:
{
  "doc_name": "название документа",
  "course_summary": "краткое описание курса (2-3 предложения)",
  "basic_questions": [
    {
      "id": 0,
      "text": "текст вопроса",
      "options": ["A. вариант ответа", "B. вариант ответа", "C. вариант ответа", "D. вариант ответа"],
      "correct": "A",
      "explanation": "краткое объяснение правильного ответа со ссылкой на стандарт"
    }
  ],
  "exam_questions": [
    {
      "id": 0,
      "text": "текст вопроса",
      "options": ["A. вариант ответа", "B. вариант ответа", "C. вариант ответа", "D. вариант ответа"],
      "correct": "B",
      "explanation": "краткое объяснение"
    }
  ]
}
Требования:
- Базовые вопросы (5): простые, проверяют знание основных фактов и правил из документа.
- Экзаменационные вопросы (10): сложнее, проверяют понимание процессов, алгоритмов, исключений.
- Ровно 4 варианта ответа (A/B/C/D), поле correct — одна из букв A, B, C или D.
- Все вопросы строго по предоставленным фрагментам. Язык: русский."""


def generate_questions(doc_name: str, chunks: list[dict]) -> dict:
    """
    Generate 5 basic + 10 exam questions from document chunks.

    Args:
        doc_name: Human-readable document/section name.
        chunks: List of chunk dicts with at least 'text' and optionally 'heading'.

    Returns:
        Parsed dict with keys: doc_name, course_summary, basic_questions, exam_questions.

    Raises:
        ValueError: If GPT fails to return valid JSON after 2 attempts.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    context = "\n\n---\n\n".join(
        f"[{c.get('heading', c.get('code', 'Раздел'))}]\n{c['text']}"
        for c in chunks[:15]
    )

    for attempt in range(2):
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": QUESTION_GEN_SYSTEM_PROMPT},
                {"role": "user", "content": f"Документ: {doc_name}\n\n{context}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=3000,
        )

        raw = response.choices[0].message.content
        try:
            result = json.loads(raw)
            _validate_questions(result)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[course_generator] Attempt {attempt + 1} failed: {e}")
            if attempt == 1:
                raise ValueError(f"GPT returned invalid questions after 2 attempts: {e}") from e

    return {}  # unreachable


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
