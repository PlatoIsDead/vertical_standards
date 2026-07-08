"""
scripts/eval_roles.py — retrieval-only проверка ролевой фильтрации.

Валидационный цикл из сметы: Дмитрий готовит вопросы с источником и ролью →
кладём их в data/eval_roles.json → гоняем скрипт → правки на ежедневном созвоне.

Формат строки eval_roles.json:
  question                — вопрос сотрудника
  role                    — role_id, от имени которого спрашиваем
  expect_substring        — должна встретиться в heading/text топ-выдачи (lower)
  forbid_heading_substring — не должна встретиться ни в одном заголовке выдачи
                             (null = не проверять)

Всегда проверяется инвариант: в выдаче нет ни одного чанка audience=guest.

ЖИВОЙ гейт: нужен OPENAI_API_KEY и сеть (embeddings). Не для CI.
Запуск: python scripts/eval_roles.py
"""
import json
import os
import sys

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.rag import cosine_sim, load_index  # noqa: E402
from app.roles import role_mask  # noqa: E402

load_dotenv()

EVAL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "eval_roles.json")
TOP_K = 16          # как в rag.answer
SCORE_MIN = 0.01    # как в rag.answer


def retrieve(client: OpenAI, query: str, role: str, chunks: list, embeddings) -> list[dict]:
    resp = client.embeddings.create(model="text-embedding-3-small", input=[query])
    qvec = np.array(resp.data[0].embedding, dtype=np.float32)
    scores = cosine_sim(qvec, embeddings) * role_mask(chunks, role)
    top = np.argsort(scores)[::-1][:TOP_K]
    return [chunks[i] for i in top if scores[i] > SCORE_MIN]


def main() -> None:
    cases = json.load(open(EVAL_PATH, encoding="utf-8"))
    chunks, embeddings = load_index()
    # Сеть WSL2 флапает — тот же паттерн retry, что в app/rag.py
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=20.0, max_retries=3)

    failures = 0
    for case in cases:
        q, role = case["question"], case["role"]
        expect = (case.get("expect_substring") or "").lower()
        forbid = (case.get("forbid_heading_substring") or "").lower()

        results = retrieve(client, q, role, chunks, embeddings)
        blob = " ".join((c["heading"] + " " + c["text"]).lower() for c in results)
        headings = [c["heading"].lower() for c in results]

        problems = []
        if expect and expect not in blob:
            problems.append(f"нет «{expect}» в топ-{TOP_K}")
        if forbid and any(forbid in h for h in headings):
            problems.append(f"в выдаче заголовок с «{forbid}»")
        guests = [c for c in results if c.get("audience") == "guest"]
        if guests:
            problems.append(f"ПРОТЕЧКА guest-чанков: {len(guests)}")
        wrong_role = [
            c for c in results
            if c.get("roles") and role not in c["roles"] and "all_staff" not in c["roles"]
        ]
        if wrong_role:
            problems.append(f"ПРОТЕЧКА чужой роли: {len(wrong_role)}")

        status = "PASS" if not problems else "FAIL"
        failures += bool(problems)
        print(f"[{status}] role={role:<16} q={q!r} ({len(results)} чанков)")
        for p in problems:
            print(f"       ! {p}")

    print(f"\nИтог: {len(cases) - failures}/{len(cases)} PASS")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
