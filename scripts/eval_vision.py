"""
scripts/eval_vision.py — петля оценки vision-ингеста (PRP vision-ingest).

Некруговая оценка: генератор вопросов и судья видят ОРИГИНАЛ медиа
(картинку / SmartArt-outline), а ответы даёт полный RAG-пайплайн по
свежему ингесту файлов (мини-индекс в памяти, боевые data/ не трогаются).
Вопросы — строго о ПОРЯДКЕ и НАПРАВЛЕНИИ (стрелки, нумерация шагов).

Порог приёмки: ≥80% correct И 0 wrong. Ниже — спринт-протокол PRP
(PRPs/vision-ingest-sprints.md).

Запуск на сервере (файлы в /state/data = /app/data, ключ в .env):
    docker exec vertical-standards-bot python scripts/eval_vision.py --date YYYYMMDD
"""
import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402

from app.doc_parsers import parse_file  # noqa: E402
from app.media_ingest import extract_media, smartart_outline  # noqa: E402
from app.rag import answer as rag_answer  # noqa: E402
from app.roles import ALL_STAFF, display_name, parse_filename  # noqa: E402

load_dotenv()

DEFAULT_FILES = (
    "/app/data/FO, RES АЛГОРИТМ БРОНИРОВАНИЯ ПО ТЕЛЕФОНУ.docx;"
    "/app/data/FO, RES, SAL Негарантированные бронирования.docx"
)

GEN_PROMPT = """Ты проверяешь, понимает ли обучающий бот визуальную часть стандарта отеля
«{ctx}». Тебе дан ОРИГИНАЛ (схема или скриншот с аннотациями). Составь РОВНО {n}
вопроса(ов) СТРОГО о порядке и направлении: «что идёт после …», «в каком случае …»,
«на какой элемент указывает стрелка/цифра …», «какой пункт меню выбрать, чтобы …».
Ответ на каждый вопрос должен однозначно следовать из оригинала.
Ответь строго JSON: {{"questions": ["...", "..."]}}. Язык: русский."""

JUDGE_PROMPT = """Ты судья. Тебе даны ОРИГИНАЛ визуального фрагмента стандарта «{ctx}», вопрос и
ответ бота. Оцени ответ ТОЛЬКО по оригиналу:
- correct — ответ верен по сути (порядок/направление/элемент совпадают);
- partial — частично верен или неполон, но без ошибок направления/порядка;
- wrong — противоречит оригиналу или выдуман.
Ответь строго JSON: {{"verdict": "correct|partial|wrong", "reason": "одно предложение"}}."""


def _image_part(image_bytes: bytes) -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    return {"type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"}}


def _vision_json(client: OpenAI, system: str, user_text: str,
                 image_bytes: bytes | None, validate=None) -> dict:
    """JSON-вызов с опциональной картинкой (2 попытки — паттерн _llm_json)."""
    content: list = [{"type": "text", "text": user_text}]
    if image_bytes:
        content.append(_image_part(image_bytes))
    for attempt in range(2):
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": content}],
            response_format={"type": "json_object"},
            max_completion_tokens=1500,
        )
        try:
            result = json.loads(resp.choices[0].message.content)
            if validate:
                validate(result)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[eval-vision] attempt {attempt + 1} failed: {e}")
            if attempt == 1:
                raise ValueError(f"bad JSON after 2 attempts: {e}") from e
    return {}


def _build_index(paths: list[str]) -> tuple[list[dict], "np.ndarray"]:
    """Свежий ингест файлов → мини-индекс в памяти (боевые data/ не трогаем)."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                    timeout=60.0, max_retries=2)
    chunks: list[dict] = []
    for path in paths:
        fname = os.path.basename(path)
        roles = parse_filename(fname)["roles"] or [ALL_STAFF]
        for c in parse_file(path, "docx", fname):
            chunks.append({**c, "roles": list(roles), "audience": "staff",
                           "doc_name": fname})
    vecs = []
    for c in chunks:
        resp = client.embeddings.create(model="text-embedding-3-small",
                                        input=[c["text"][:2000]])
        vecs.append(resp.data[0].embedding)
    return chunks, np.array(vecs, dtype=np.float32)


def _collect_media(paths: list[str]) -> list[dict]:
    """[{kind: schema|image, original: bytes|outline, file, label}]."""
    items = []
    for path in paths:
        fname = os.path.basename(path)
        diagrams, images = extract_media(path)
        for i, d in enumerate(diagrams, 1):
            items.append({"kind": "schema", "outline": smartart_outline(d),
                          "image": None, "file": fname,
                          "label": f"Схема {i}"})
        for i, img in enumerate(images, 1):
            items.append({"kind": "image", "outline": None, "image": img,
                          "file": fname, "label": f"Скриншот {i}"})
    return items


def main() -> None:
    ap = argparse.ArgumentParser(description="Оценка vision-ингеста")
    ap.add_argument("--date", required=True)
    ap.add_argument("--questions", type=int, default=4)
    ap.add_argument("--files", default=DEFAULT_FILES,
                    help="пути через «;»")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    args = ap.parse_args()
    paths = [p for p in args.files.split(";") if p.strip()]

    print("[eval-vision] строю мини-индекс…")
    chunks, emb = _build_index(paths)
    print(f"[eval-vision] чанков: {len(chunks)} "
          f"(медиа: {sum(1 for c in chunks if c['text'].startswith('[С'))})")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                    timeout=90.0, max_retries=2)
    media = _collect_media(paths)
    lines = [f"# Оценка vision-ингеста — {args.date}", "",
             f"Медиа-объектов: {len(media)} · вопросов на объект: "
             f"{args.questions} · модель: {os.getenv('OPENAI_MODEL')}", ""]
    stats = {"correct": 0, "partial": 0, "wrong": 0}

    for m in media:
        ctx = display_name(m["file"])
        role = next((r for r in parse_filename(m["file"])["roles"]
                     if r != ALL_STAFF), None)
        lines += [f"## {m['label']} — {ctx}", ""]
        user = ("Оригинал-схема (outline):\n" + m["outline"]
                if m["kind"] == "schema" else "Оригинал — на изображении.")

        def _chk(r, n=args.questions):
            if not isinstance(r.get("questions"), list) or \
                    len(r["questions"]) != n:
                raise ValueError(f"нужно {n} вопросов")

        gen = _vision_json(client, GEN_PROMPT.format(ctx=ctx,
                                                     n=args.questions),
                           user, m["image"], validate=_chk)
        for i, q in enumerate(gen["questions"], 1):
            print(f"[eval-vision] {m['label']}: вопрос {i}")
            try:
                reply, _rel = rag_answer(
                    query=q, chunks=chunks, embeddings=emb,
                    section_filter=None, answer_length="Стандартно",
                    role_filter=role)
            except Exception as exc:
                reply = f"❌ ошибка прогона: {exc!r}"
            judge_user = (f"Вопрос: {q}\n\nОтвет бота:\n{reply}"
                          + ("\n\nОригинал-схема (outline):\n" + m["outline"]
                             if m["kind"] == "schema" else ""))

            def _vchk(r):
                if r.get("verdict") not in ("correct", "partial", "wrong"):
                    raise ValueError("кривой verdict")

            verdict = _vision_json(client, JUDGE_PROMPT.format(ctx=ctx),
                                   judge_user, m["image"], validate=_vchk)
            stats[verdict["verdict"]] += 1
            mark = {"correct": "✅", "partial": "🟡",
                    "wrong": "❌"}[verdict["verdict"]]
            lines += [f"**В{i}. {q}**", "", reply, "",
                      f"{mark} **{verdict['verdict']}** — "
                      f"{verdict.get('reason', '')}", "", "---", ""]

    total = sum(stats.values()) or 1
    pct = 100 * stats["correct"] // total
    passed = pct >= 80 and stats["wrong"] == 0
    lines += ["## Итог", "",
              f"correct: {stats['correct']} · partial: {stats['partial']} · "
              f"wrong: {stats['wrong']} → **{pct}% correct**",
              "",
              ("✅ ПОРОГ ПРОЙДЕН (≥80% и 0 wrong)" if passed
               else "❌ ПОРОГ НЕ ПРОЙДЕН — спринт-протокол "
                    "PRPs/vision-ingest-sprints.md")]
    out_path = os.path.join(args.out, f"eval_vision_{args.date}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[-6:]))
    print(f"Отчёт: {out_path}")


if __name__ == "__main__":
    main()
