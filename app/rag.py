"""
app/rag.py — OpenAI only, clean version
"""
import json
import os
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv

from app.roles import role_mask

load_dotenv()

DATA_DIR        = os.path.join(os.path.dirname(__file__), "..", "data")
CHUNKS_PATH     = os.path.join(DATA_DIR, "chunks_cache.json")
EMBEDDINGS_PATH = os.path.join(DATA_DIR, "embeddings_cache.npy")

SYSTEM_PROMPT = """Ты — корпоративный ассистент сети апарт-отелей «Вертикаль».
Ты отвечаешь СОТРУДНИКУ отеля, а не гостю: инструкции, адресованные гостям,
не выдавай как инструкции для сотрудника.
Отвечай ТОЛЬКО на основе предоставленных фрагментов документа.
Фрагменты «[Скриншот N]» и «[Схема]» описывают РАЗНЫЕ изображения: отвечая
про конкретный скриншот, элемент или пометку, бери детали только из ТОГО
фрагмента, где нашёл ответ — не переноси подписи и значения с одного
скриншота на другой (vision-ингест, Sprint 4).
Отвечай на русском языке, чётко и по делу.
Если ответа в документе нет — скажи об этом прямо.
Не придумывай информацию."""

def load_index():
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    embeddings = np.load(EMBEDDINGS_PATH)
    return chunks, embeddings

def cosine_sim(a, b):
    a = a / (np.linalg.norm(a) + 1e-10)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return b @ a

def answer(query, chunks, embeddings, section_filter, answer_length, role_filter=None):
    # Flapping WSL2 network times out on OpenAI; SDK does exponential backoff itself.
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=20.0, max_retries=3)

    # Embed query
    resp = client.embeddings.create(model="text-embedding-3-small", input=[query])
    qvec = np.array(resp.data[0].embedding, dtype=np.float32)

    scores = cosine_sim(qvec, embeddings)

    if section_filter and section_filter != "Общее":
        mask = np.array([1.0 if c["section"] == section_filter else 0.0 for c in chunks])
        scores = scores * mask

    # Ролевая маска применяется всегда: при role_filter=None она исключает
    # только audience=guest (бот отвечает сотрудникам, не гостям).
    scores = scores * role_mask(chunks, role_filter)

    top = np.argsort(scores)[::-1][:16]
    relevant = [{**chunks[i], "score": float(scores[i])} for i in top if scores[i] > 0.01]

    if not relevant:
        return "Не найдено релевантных фрагментов.", []

    context = "\n\n---\n\n".join(
        f"[{c['section']} | {c['heading']}]\n{c['text']}"
        for i, c in enumerate(relevant, 1)
    )

    length_map = {
        "Коротко":    "Ответ — 2-3 предложения.",
        "Стандартно": "Ответ — до 150 слов.",
        "Подробно":   "Развёрнутый ответ со всеми деталями.",
    }

    # gpt-5.5 (OPENAI_MODEL с 17.08) отвергает max_tokens и temperature —
    # max_completion_tokens понимают и новые, и старые модели (18.08: живой
    # 400 «Unsupported parameter: max_tokens» ронял ВСЕ RAG-ответы)
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{length_map.get(answer_length, '')}\n\nФрагменты:\n{context}\n\nВопрос: {query}"},
        ],
        max_completion_tokens=2000,
    )
    return response.choices[0].message.content, relevant
