#!/usr/bin/env python3
"""
scripts/generate_questions.py — PoC: generate onboarding questions from chunks_cache.json

Usage:
    python scripts/generate_questions.py --section "Служба приёма и размещения"
    python scripts/generate_questions.py --list-sections
"""
import argparse
import json
import os
import sys

# Add project root to path so app.* imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from app.course_generator import generate_questions

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CHUNKS_PATH = os.path.join(DATA_DIR, "chunks_cache.json")
COURSES_DIR = os.path.join(DATA_DIR, "courses")


def load_all_chunks() -> list[dict]:
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def list_sections(all_chunks: list[dict]) -> list[str]:
    return sorted(set(c.get("section", "Общее") for c in all_chunks))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Генерация вопросов для онбординга из chunks_cache.json"
    )
    parser.add_argument("--section", help="Раздел для генерации (--list-sections покажет все)")
    parser.add_argument("--list-sections", action="store_true", help="Показать доступные разделы")
    args = parser.parse_args()

    all_chunks = load_all_chunks()

    if args.list_sections:
        sections = list_sections(all_chunks)
        print(f"Доступных разделов: {len(sections)}")
        for s in sections:
            count = sum(1 for c in all_chunks if c.get("section") == s)
            print(f"  {s!r:45s}  ({count} чанков)")
        return

    if not args.section:
        parser.print_help()
        sys.exit(1)

    chunks = [c for c in all_chunks if c.get("section") == args.section]
    if not chunks:
        print(f"Раздел не найден: {args.section!r}")
        print("\nДоступные разделы:")
        for s in list_sections(all_chunks):
            print(f"  {s}")
        sys.exit(1)

    print(f"Раздел: {args.section!r} ({len(chunks)} чанков)")
    print("Генерируем вопросы через GPT...")

    questions = generate_questions(args.section, chunks)

    os.makedirs(COURSES_DIR, exist_ok=True)
    safe_name = args.section.replace(" ", "_").replace("/", "_")
    out_path = os.path.join(COURSES_DIR, f"{safe_name}_draft.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

    n_basic = len(questions.get("basic_questions", []))
    n_exam = len(questions.get("exam_questions", []))
    print(f"\n✅ Сохранено → {out_path}")
    print(f"   Базовых вопросов: {n_basic}")
    print(f"   Экзаменационных: {n_exam}")

    if questions.get("course_summary"):
        print(f"\nКраткое описание:\n  {questions['course_summary']}")

    if questions.get("basic_questions"):
        q = questions["basic_questions"][0]
        print("\nПример (базовый вопрос 1):")
        print(f"  {q['text']}")
        for opt in q["options"]:
            marker = " ✓" if opt.startswith(q["correct"] + ".") else ""
            print(f"    {opt}{marker}")


if __name__ == "__main__":
    main()
