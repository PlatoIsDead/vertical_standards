"""
scripts/build_index.py
Reads standards.docx, splits into chunks by section, saves cache.
Run once: python scripts/build_index.py
"""

import json
import os
import re
import numpy as np
from docx import Document
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCX_PATH = os.path.join(DATA_DIR, "standards.docx")
CHUNKS_PATH = os.path.join(DATA_DIR, "chunks_cache.json")
EMBEDDINGS_PATH = os.path.join(DATA_DIR, "embeddings_cache.npy")

# Section tags — used for filtering in the UI
SECTION_TAGS = {
    "VA.ADM.3": "Чрезвычайные ситуации",
    "VA.HR":    "Управление персоналом",
    "VA.FO":    "Служба приёма и размещения",
    "VA.OW":    "Работа с собственниками",
    "VA.SD":    "Отдел продаж",
    "VA.MD":    "Маркетинг и продвижение",
    "VA.HSK":   "Хозяйственная служба",
    "VA.ADM":   "Административная политика",  # catch-all, after VA.ADM.3
}

CHUNK_SIZE = 600    # tokens approx (chars / 4)
CHUNK_OVERLAP = 80


def tag_section(heading: str) -> str:
    """Return human-readable section name from heading code."""
    for code, name in SECTION_TAGS.items():
        if code in heading.upper():
            return name
    return "Общее"


def extract_chunks(docx_path: str) -> list[dict]:
    """
    Extract text from docx, split into overlapping chunks.
    Each chunk carries: text, section, heading.
    """
    doc = Document(docx_path)
    chunks = []
    current_heading = "Общее"
    current_section = "Общее"
    buffer = []
    buffer_len = 0

    def flush(heading, section):
        text = " ".join(buffer).strip()
        if len(text) > 80:
            chunks.append({
                "text": text,
                "heading": heading,
                "section": section,
            })

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        style = para.style.name if para.style else ""
        is_heading = "Heading" in style or re.match(r"^VA\.[A-Z]", text)

        if is_heading:
            # Save previous buffer
            if buffer:
                flush(current_heading, current_section)
                # Keep overlap
                overlap_text = " ".join(buffer[-3:])
                buffer = [overlap_text] if overlap_text else []
                buffer_len = len(overlap_text) // 4
            current_heading = text[:120]
            current_section = tag_section(text)
            buffer.append(text)
            buffer_len += len(text) // 4
        else:
            buffer.append(text)
            buffer_len += len(text) // 4

            if buffer_len >= CHUNK_SIZE:
                flush(current_heading, current_section)
                # Keep overlap
                overlap_text = " ".join(buffer[-3:])
                buffer = [overlap_text] if overlap_text else []
                buffer_len = len(overlap_text) // 4

    if buffer:
        flush(current_heading, current_section)

    print(f"Extracted {len(chunks)} chunks across sections:")
    from collections import Counter
    counts = Counter(c["section"] for c in chunks)
    for sec, cnt in sorted(counts.items()):
        print(f"  {sec}: {cnt} chunks")

    return chunks


def embed_chunks(chunks: list[dict]) -> np.ndarray:
    import requests as req
    texts = [c["text"][:6000] for c in chunks]
    print(f"Embedding {len(texts)} chunks via Ollama...")
    url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/embed"
    model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    all_embeddings = []
    dim = None
    for i, text in enumerate(texts):
        try:
            text_clean = text.strip()
            if not text_clean:
                text_clean = "пустой фрагмент"
            r = req.post(url, json={"model": model, "input": text_clean}, timeout=30)
            r.raise_for_status()
            emb = r.json()["embeddings"][0]
            dim = len(emb)
            all_embeddings.append(emb)
        except Exception as e:
            print(f"  SKIP chunk {i}: {e}")
            all_embeddings.append([0.0] * (dim or 768))
        if (i + 1) % 50 == 0:
            print(f"  Embedded {i+1}/{len(texts)}")
    print(f"  Embedded {len(texts)}/{len(texts)}")
    return np.array(all_embeddings, dtype=np.float32)


def build_index():
    print("=== Building Vertical Standards RAG Index ===")
    chunks = extract_chunks(DOCX_PATH)

    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"Chunks saved: {CHUNKS_PATH}")

    embeddings = embed_chunks(chunks)
    np.save(EMBEDDINGS_PATH, embeddings)
    print(f"Embeddings saved: {EMBEDDINGS_PATH}")
    print("Done! Run: streamlit run app/streamlit_app.py")


if __name__ == "__main__":
    build_index()
