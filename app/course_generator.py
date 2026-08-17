"""
app/course_generator.py — GPT-based question generation for staff onboarding courses.

Демо-фидбек D (05.08): генерация в ДВА этапа — извлечь 15 ключевых положений,
потом вопросы по ним. Фидбек 17.08 (живой дамп боевой БД): + этап-критик,
+ перемешивание правильной буквы КОДОМ (модель клала correct=A во все вопросы),
+ запрет мета-вопросов, + контекст = весь документ, + facts сохраняются в
questions_json (топливо точечной перегенерации «Перегенерировать N.q»).

Совместимость с gpt-5.5: max_completion_tokens (max_tokens отвергается новыми
моделями), temperature не передаётся вовсе.
"""
import json
import os
import random
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MAX_CONTEXT_CHARS = 120_000  # весь документ; кап — защита от гигантов

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
- Каждое положение — конкретное проверяемое утверждение ИЗ ТЕКСТА документа и обязано
  содержать хотя бы один конкретный элемент: шаг процедуры, число или порог, срок,
  роль/ответственного либо конкретное действие (кому позвонить, что нажать, что записать).
- ЗАПРЕЩЕНЫ положения о самом документе (его название, назначение, целевая аудитория,
  «документ описывает…») — только содержание работы.
- Язык: русский."""

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
- Ровно 4 варианта. ПРАВИЛЬНЫЙ ответ ВСЕГДА клади в вариант A (correct всегда "A") —
  порядок вариантов потом перемешает программа.
- Вопрос проверяет ЗНАНИЕ РАБОТЫ, а не память о документе. ЗАПРЕЩЕНЫ вопросы, на которые
  можно ответить не читая документ: «что описывает документ», «кто целевая аудитория»,
  «как называется…», вопросы на общий здравый смысл.
- Неверные варианты — правдоподобные ОШИБКИ ИСПОЛНЕНИЯ из этого же документа: соседний
  шаг процедуры, другой порог или срок из текста, чужая зона ответственности.
  Никаких очевидно абсурдных вариантов.
- explanation ссылается на содержание стандарта, НЕ на буквы вариантов (буквы изменятся).
- Язык: русский."""

CRITIC_PROMPT = """Ты проверяешь качество теста по внутреннему стандарту апарт-отеля.
Даны фрагменты документа и пронумерованные вопросы теста. Для КАЖДОГО вопроса реши,
годен ли он. Вопрос БРАКУЕТСЯ, если: на него можно ответить, не читая документ
(мета-вопрос о названии/назначении/аудитории документа, вопрос на здравый смысл);
или неверные варианты очевидно абсурдны либо неотличимы от верного;
или вопрос не проверяет конкретное положение документа.
Ответь строго в JSON формате:
{"verdicts": [{"num": 1, "ok": true, "reason": ""}]}
— по одному вердикту на каждый вопрос; num — номер из входа; reason заполняй
только для ok=false. Язык: русский."""

REGEN_ONE_PROMPT = """Ты составляешь ОДИН вопрос теста для сотрудников апарт-отелей «Вертикаль».
Ответь строго в JSON формате:
{
  "text": "текст вопроса",
  "options": ["A. вариант", "B. вариант", "C. вариант", "D. вариант"],
  "correct": "A",
  "explanation": "краткое объяснение правильного ответа со ссылкой на стандарт"
}
Требования:
- ПРАВИЛЬНЫЙ ответ всегда в варианте A (correct всегда "A") — порядок перемешает программа.
- Вопрос НЕ повторяет уже существующие вопросы теста (их список дан).
- ЗАПРЕЩЕНЫ вопросы, на которые можно ответить не читая документ (мета-вопросы о
  названии/назначении/аудитории, здравый смысл).
- Неверные варианты — правдоподобные ошибки исполнения из этого же документа.
- explanation без ссылок на буквы вариантов. Язык: русский."""


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
            max_completion_tokens=max_tokens,
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


def _build_context(chunks: list[dict]) -> str:
    context = "\n\n---\n\n".join(
        f"[{c.get('heading', c.get('code', 'Раздел'))}]\n{c['text']}"
        for c in chunks
    )
    if len(context) > MAX_CONTEXT_CHARS:
        print(f"[course_generator] context truncated "
              f"{len(context)} → {MAX_CONTEXT_CHARS}")
        context = context[:MAX_CONTEXT_CHARS]
    return context


_OPT_PREFIX_RE = re.compile(r"^([A-D])[.)]\s*(.*)$", re.DOTALL)
_LETTERS = ("A", "B", "C", "D")


def _shuffle_options(q: dict) -> dict:
    """Перемешать варианты КОДОМ и пересчитать correct.

    Модели детерминированно велено класть верный ответ в A (наблюдение 17.08:
    просить «случайные буквы» бесполезно — correct=A выходил у всех вопросов),
    рандом — здесь. Кривые префиксы → вопрос не трогаем (лог), валидация решит."""
    parsed = [_OPT_PREFIX_RE.match(str(o).strip()) for o in q.get("options", [])]
    if len(parsed) != 4 or not all(parsed):
        print(f"[course_generator] shuffle skip (кривые варианты): {q.get('options')}")
        return q
    bodies = {m.group(1): m.group(2).strip() for m in parsed}
    if len(bodies) != 4 or q.get("correct") not in bodies:
        print(f"[course_generator] shuffle skip (буквы/correct): {q.get('options')}")
        return q
    correct_body = bodies[q["correct"]]
    shuffled = list(bodies.values())
    random.shuffle(shuffled)
    q["options"] = [f"{letter}. {body}" for letter, body in zip(_LETTERS, shuffled)]
    q["correct"] = _LETTERS[shuffled.index(correct_body)]
    return q


def generate_questions(doc_name: str, chunks: list[dict]) -> dict:
    """
    Генерация 5 базовых + 10 экзаменационных вопросов в три этапа.

    Этап 1: 15 ключевых положений документа (+course_summary).
    Этап 2: вопрос с 4 вариантами на каждое положение; буквы перемешивает код.
    Этап 3: критик бракует полые вопросы → один ремонтный раунд (best-effort).

    Returns:
        dict прежней схемы + facts_basic/facts_exam: doc_name, course_summary,
        basic_questions[5], exam_questions[10], facts_basic[5], facts_exam[10].

    Raises:
        ValueError: если LLM не вернул валидный JSON после ретраев этапов 1–2
        (сбой критика/ремонта генерацию НЕ роняет).
    """
    # timeout/retries — конвенция репо (rag.py): сеть WSL2 флапает, без таймаута
    # вызов может висеть; 60с — генерация тяжелее эмбеддингов
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                    timeout=60.0, max_retries=2)

    context = _build_context(chunks)

    facts = _llm_json(
        client, FACTS_PROMPT,
        f"Документ: {doc_name}\n\n{context}",
        max_tokens=2000, validate=_validate_facts,
    )
    facts_json = json.dumps(
        {"facts_basic": facts["facts_basic"], "facts_exam": facts["facts_exam"]},
        ensure_ascii=False,
    )
    result = _llm_json(
        client, QUESTIONS_FROM_FACTS_PROMPT,
        f"Документ: {doc_name}\n\nПоложения:\n{facts_json}\n\n"
        f"Контекст документа (для правдоподобных неверных вариантов):\n{context}",
        max_tokens=6000, validate=_validate_questions,
    )
    for q in result["basic_questions"] + result["exam_questions"]:
        _shuffle_options(q)

    _run_critic(client, doc_name, context, facts, result)

    result["doc_name"] = doc_name
    result["course_summary"] = facts.get("course_summary", "")
    result["facts_basic"] = facts["facts_basic"]
    result["facts_exam"] = facts["facts_exam"]
    return result


def _run_critic(client: OpenAI, doc_name: str, context: str,
                facts: dict, result: dict) -> None:
    """Этап 3: критик + один ремонтный раунд. Best-effort: любой сбой —
    лог, результат этапа 2 остаётся как есть."""
    all_q = result["basic_questions"] + result["exam_questions"]
    facts_all = facts["facts_basic"] + facts["facts_exam"]
    try:
        numbered = "\n\n".join(
            f"{i}. {q['text']}\n" + "\n".join(str(o) for o in q["options"])
            + f"\nОтвет: {q['correct']}"
            for i, q in enumerate(all_q, 1)
        )
        critic = _llm_json(
            client, CRITIC_PROMPT,
            f"Документ: {doc_name}\n\n{context}\n\nВопросы теста:\n\n{numbered}",
            max_tokens=2000, validate=_validate_verdicts,
        )
    except ValueError as e:
        print(f"[course_generator] критик не отработал — пропускаю: {e}")
        return

    bad = {int(v["num"]): str(v.get("reason", ""))
           for v in critic["verdicts"]
           if not v.get("ok") and isinstance(v.get("num"), int)}
    if not bad:
        return
    print(f"[course_generator] критик забраковал {sorted(bad)} — ремонт")
    existing_texts = [q["text"] for q in all_q]
    for num, reason in bad.items():
        if not (1 <= num <= 15):
            continue
        q_list, idx = ((result["basic_questions"], num - 1) if num <= 5
                       else (result["exam_questions"], num - 6))
        try:
            new_q = _regen_one_call(client, doc_name, context,
                                    facts_all[num - 1], existing_texts, reason)
        except ValueError as e:
            print(f"[course_generator] ремонт вопроса {num} не удался: {e}")
            continue  # принять как есть — не зацикливаться
        new_q["id"] = q_list[idx].get("id", idx)
        q_list[idx] = new_q


def _regen_one_call(client: OpenAI, doc_name: str, context: str,
                    fact: str | None, existing_texts: list[str],
                    reason: str = "") -> dict:
    user = f"Документ: {doc_name}\n\n"
    if fact:
        user += f"Положение, которое должен проверять вопрос:\n{fact}\n\n"
    if reason:
        user += f"Прежний вопрос забракован, причина: {reason}\n\n"
    user += ("Уже существующие вопросы теста (НЕ повторять):\n- "
             + "\n- ".join(existing_texts)
             + f"\n\nКонтекст документа:\n{context}")
    q = _llm_json(client, REGEN_ONE_PROMPT, user,
                  max_tokens=1500, validate=validate_question)
    return _shuffle_options(q)


def regenerate_one(doc_name: str, chunks: list[dict], fact: str | None,
                   existing_texts: list[str]) -> dict:
    """«Перегенерировать N.q»: один вопрос по положению (или по документу,
    если положений нет — старые курсы без facts_*)."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                    timeout=60.0, max_retries=2)
    return _regen_one_call(client, doc_name, _build_context(chunks),
                           fact, existing_texts)


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


_OPT_STRIP_RE = re.compile(r"^[A-DА-Д][.)]\s*")


def validate_question(q: dict) -> None:
    """Raise ValueError if a single question dict is invalid.

    Используется генератором (все 15), правкой HR (№3/визард 17.08) и
    перегенерацией. Тексты ошибок по-русски — их видит HR при сохранении."""
    missing = [k for k in ("text", "options", "correct") if k not in q]
    if missing:
        raise ValueError(f"У вопроса нет полей {missing}")
    if not str(q["text"]).strip():
        raise ValueError("Пустой текст вопроса")
    if len(q["options"]) != 4:
        raise ValueError(f"Нужно 4 варианта ответа, получено {len(q['options'])}")
    bodies = []
    for o in q["options"]:
        body = _OPT_STRIP_RE.sub("", str(o).strip())
        if not body:
            raise ValueError(f"Пустой вариант ответа: {o!r}")
        bodies.append(body.casefold())
    if len(set(bodies)) != 4:
        raise ValueError("Варианты ответа повторяются")
    if q["correct"] not in ("A", "B", "C", "D"):
        raise ValueError(f"Правильный ответ должен быть A/B/C/D, "
                         f"получено {q['correct']!r}")


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


def _validate_verdicts(result: dict) -> None:
    verdicts = result.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        raise ValueError("verdicts пуст или не список")
    for v in verdicts:
        if not isinstance(v, dict) or "num" not in v or "ok" not in v:
            raise ValueError(f"кривой вердикт: {v}")
