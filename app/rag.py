"""
app/rag.py — OpenAI only, clean version
"""
import json, os
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

DATA_DIR        = os.path.join(os.path.dirname(__file__), "..", "data")
CHUNKS_PATH     = os.path.join(DATA_DIR, "chunks_cache.json")
EMBEDDINGS_PATH = os.path.join(DATA_DIR, "embeddings_cache.npy")

SYSTEM_PROMPT = """Ты — корпоративный ассистент сети апарт-отелей «Вертикаль».
Отвечай ТОЛЬКО на основе предоставленных фрагментов документа.
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

def answer(query, chunks, embeddings, section_filter, answer_length):
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Embed query
    resp = client.embeddings.create(model="text-embedding-3-small", input=[query])
    qvec = np.array(resp.data[0].embedding, dtype=np.float32)

    scores = cosine_sim(qvec, embeddings)

    if section_filter and section_filter != "Общее":
        mask = np.array([1.0 if c["section"] == section_filter else 0.0 for c in chunks])
        scores = scores * mask

    top = np.argsort(scores)[::-1][:6]
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

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{length_map.get(answer_length, '')}\n\nФрагменты:\n{context}\n\nВопрос: {query}"},
        ],
        temperature=0.2,
        max_tokens=800,
    )
    return response.choices[0].message.content, relevant
