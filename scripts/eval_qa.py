"""
scripts/eval_qa.py — валидационный прогон Q&A (19.08, план юзера вместо
ожидания вопросов от Дмитрия): для каждого документа индекса gpt генерирует
N конкретных вопросов ПО СОДЕРЖИМОМУ, каждый прогоняется через боевой RAG в
роли документа; markdown-отчёт «вопрос → ответ → источник» — на проверку
юзеру, затем Дмитрию.

Запуск на сервере (данные и ключ в контейнере):
    docker exec vertical-standards-bot python scripts/eval_qa.py --date 20260820
Отчёт: /app/data/eval_qa_{date}.md (+ stdout).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402

from app.course_generator import _llm_json  # noqa: E402
from app.rag import answer as rag_answer  # noqa: E402
from app.rag import load_index  # noqa: E402
from app.roles import ALL_STAFF, display_name, parse_filename  # noqa: E402

load_dotenv()

MIN_WORDS = 50  # меньше — документ фактически без текста (картинки)

QUESTIONS_PROMPT = """Ты составляешь проверочные вопросы для сотрудников апарт-отелей «Вертикаль».
По тексту стандарта составь РОВНО {n} конкретных вопросов, которые сотрудник
мог бы задать в работе. Ответь строго в JSON формате:
{{"questions": ["...", "..."]}}
Требования:
- Вопрос — о СОДЕРЖАНИИ работы: шаги процедуры, пороги, сроки, кому звонить,
  зоны ответственности.
- ЗАПРЕЩЕНЫ вопросы о самом документе («что описывает документ», «кто целевая
  аудитория», «как называется…») и вопросы на общий здравый смысл.
- Ответ на каждый вопрос должен содержаться в тексте. Язык: русский."""


def main() -> None:
    ap = argparse.ArgumentParser(description="Валидационный прогон Q&A")
    ap.add_argument("--date", required=True, help="дата в имя отчёта, YYYYMMDD")
    ap.add_argument("--questions", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    args = ap.parse_args()

    chunks, embeddings = load_index()
    docs: dict[str, list[dict]] = {}
    for c in chunks:
        name = c.get("doc_name")
        if name:
            docs.setdefault(name, []).append(c)
    if not docs:
        raise SystemExit("Индекс пуст — нечего проверять.")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                    timeout=60.0, max_retries=2)
    lines = [f"# Валидация Q&A — {args.date}", "",
             f"Документов: {len(docs)} · вопросов на документ: "
             f"{args.questions} · модель: "
             f"{os.getenv('OPENAI_MODEL', 'gpt-4o-mini')}", ""]

    for doc_name in sorted(docs):
        doc_chunks = docs[doc_name]
        text = "\n\n".join(c.get("text", "") for c in doc_chunks)
        roles = parse_filename(doc_name)["roles"]
        role = next((r for r in roles if r != ALL_STAFF), None)
        lines += [f"## {display_name(doc_name)}",
                  f"_Файл: {doc_name} · роль прогона: {role or 'ALL'}_", ""]
        if len(text.split()) < MIN_WORDS:
            lines += ["⚠️ **Текста в документе почти нет "
                      f"({len(text.split())} слов) — бот по нему слеп** "
                      "(вероятно, содержимое в картинках).", ""]
            print(f"[eval] {doc_name}: пропуск — текста нет")
            continue

        def _check(result: dict) -> None:
            qs = result.get("questions")
            if not isinstance(qs, list) or len(qs) != args.questions:
                raise ValueError(f"нужно {args.questions} вопросов, "
                                 f"получено {qs!r}")

        gen = _llm_json(client, QUESTIONS_PROMPT.format(n=args.questions),
                        f"Стандарт «{display_name(doc_name)}»:\n\n{text}",
                        max_tokens=2000, validate=_check)
        for i, q in enumerate(gen["questions"], 1):
            print(f"[eval] {doc_name[:40]}: вопрос {i}")
            try:
                reply, relevant = rag_answer(
                    query=q, chunks=chunks, embeddings=embeddings,
                    section_filter=None, answer_length="Стандартно",
                    role_filter=role)
            except Exception as exc:  # сеть/LLM — фиксируем, не падаем
                reply, relevant = f"❌ ошибка прогона: {exc!r}", []
            src = (relevant[0].get("doc_name", "?") if relevant else "—")
            lines += [f"**В{i}. {q}**", "", reply, "",
                      f"_Источник top-1: {src}_", "", "---", ""]

    out_path = os.path.join(args.out, f"eval_qa_{args.date}.md")
    report = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nОтчёт: {out_path} ({len(report)} символов)")
    print(report)


if __name__ == "__main__":
    main()
