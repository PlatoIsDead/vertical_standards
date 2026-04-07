"""
scripts/build_index_openai.py
Reads Vertical_franchise_standards.md, builds chunks, embeds with OpenAI.
Run: python scripts/build_index_openai.py
"""
import json, os, re, time
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv
import tiktoken

load_dotenv()

DATA_DIR        = os.path.join(os.path.dirname(__file__), "..", "data")
MD_PATH         = os.path.join(DATA_DIR, "Vertical_franchise_standards.md")
CHUNKS_PATH     = os.path.join(DATA_DIR, "chunks_cache.json")
EMBEDDINGS_PATH = os.path.join(DATA_DIR, "embeddings_cache.npy")
MODEL           = "text-embedding-3-small"
MAX_TOKENS      = 512
CHUNK_SIZE      = 400
ENC             = tiktoken.get_encoding("cl100k_base")

SECTION_TAGS = {
    "VA.ADM.3": "Чрезвычайные ситуации",
    "VA.HR":    "Управление персоналом",
    "VA.FO":    "Служба приёма и размещения",
    "VA.OW":    "Работа с собственниками",
    "VA.SD":    "Отдел продаж",
    "VA.MD":    "Маркетинг и продвижение",
    "VA.HSK":   "Хозяйственная служба",
    "VA.ADM":   "Административная политика",
}

def tag_section(h):
    h_up = h.upper()
    for code, name in SECTION_TAGS.items():
        if code in h_up:
            return name
    return "Общее"

def clean_line(line):
    # Remove markdown link noise: [text](#anchor) -> text
    line = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', line)
    # Remove heading anchors {#...}
    line = re.sub(r'\{[^}]*\}', '', line)
    # Remove excessive bold markers
    line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
    return line.strip()

def is_toc_line(line):
    # Table of contents lines have pattern: text [number](#anchor)
    return bool(re.search(r'\[[\d]+\]\(#', line))

def trim_tokens(text):
    tokens = ENC.encode(text)
    if len(tokens) > MAX_TOKENS:
        tokens = tokens[:MAX_TOKENS]
    return ENC.decode(tokens)

def extract_chunks():
    with open(MD_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    chunks = []
    heading = "Общее"
    section = "Общее"
    buffer  = []
    in_toc  = True  # Skip table of contents at top

    def flush():
        text = " ".join(buffer).strip()
        # Skip very short chunks and TOC-looking text
        if len(text) > 100 and not is_toc_line(text):
            chunks.append({
                "text":    text,
                "heading": heading,
                "section": section,
            })

    for line in lines:
        line = line.rstrip()
        if not line:
            continue

        cleaned = clean_line(line)
        if not cleaned:
            continue

        # Detect headings
        h_match = re.match(r'^(#{1,3})\s+(.+)', line)
        if h_match:
            h_text = clean_line(h_match.group(2))

            # Skip empty or TOC headings
            if not h_text or h_text in ("Оглавление",):
                continue

            # Check if we're past the TOC (first real VA. heading)
            if re.search(r'VA\.[A-Z]', h_text):
                in_toc = False

            if in_toc:
                continue

            # Flush previous buffer
            if buffer:
                flush()
                overlap = buffer[-2:] if len(buffer) >= 2 else buffer[:]
                buffer  = overlap[:]

            heading = h_text[:120]
            section = tag_section(h_text)
            buffer.append(h_text)
            continue

        if in_toc:
            continue

        # Skip TOC-style lines
        if is_toc_line(line):
            continue

        buffer.append(cleaned)

        # Flush when buffer gets big enough
        token_count = len(ENC.encode(" ".join(buffer)))
        if token_count >= CHUNK_SIZE:
            flush()
            overlap = buffer[-2:] if len(buffer) >= 2 else buffer[:]
            buffer  = overlap[:]

    if buffer:
        flush()

    # Stats
    from collections import Counter
    counts = Counter(c["section"] for c in chunks)
    print(f"Extracted {len(chunks)} chunks:")
    for sec, cnt in sorted(counts.items()):
        print(f"  {sec}: {cnt}")
    return chunks

def embed_chunks(chunks):
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    texts  = [trim_tokens(c["text"]) for c in chunks]
    print(f"\nEmbedding {len(texts)} chunks...")

    embeddings = []
    for i, text in enumerate(texts):
        try:
            resp = client.embeddings.create(model=MODEL, input=[text])
            embeddings.append(resp.data[0].embedding)
        except Exception as e:
            print(f"  ERROR chunk {i}: {e} — zeros")
            embeddings.append([0.0] * 1536)
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(texts)}")
            time.sleep(0.3)

    print(f"  {len(texts)}/{len(texts)} done")
    return np.array(embeddings, dtype=np.float32)

def build_index():
    print("=== Building index from Markdown [OpenAI] ===")
    chunks = extract_chunks()

    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"Chunks saved → {CHUNKS_PATH}")

    emb = embed_chunks(chunks)
    np.save(EMBEDDINGS_PATH, emb)
    print(f"Embeddings saved → {EMBEDDINGS_PATH}")
    print("\nDone! Run: streamlit run app/streamlit_app.py")

if __name__ == "__main__":
    build_index()
