"""
scripts/dedup_index.py — remove duplicate chunks from the RAG index.

The index (data/chunks_cache.json + data/embeddings_cache.npy) accumulated
duplicate chunks from repeated document ingestion. Duplicates crowd out the
top-k retrieval budget. This keeps the first occurrence of each unique text and
drops the rest, aligning the embeddings array by the same indices.

Backs up both files to *.bak before writing.
"""
import json
import os
import shutil

import numpy as np

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
CHUNKS = os.path.join(DATA, "chunks_cache.json")
EMB = os.path.join(DATA, "embeddings_cache.npy")


def main() -> None:
    chunks = json.load(open(CHUNKS, encoding="utf-8"))
    emb = np.load(EMB)
    assert len(chunks) == emb.shape[0], (len(chunks), emb.shape)
    print(f"before: {len(chunks)} chunks, emb {emb.shape}")

    seen = set()
    keep_idx = []
    for i, c in enumerate(chunks):
        t = c.get("text", "")
        if t in seen:
            continue
        seen.add(t)
        keep_idx.append(i)

    new_chunks = [chunks[i] for i in keep_idx]
    new_emb = emb[keep_idx]
    print(f"after:  {len(new_chunks)} chunks, emb {new_emb.shape}  (removed {len(chunks) - len(new_chunks)})")

    shutil.copy2(CHUNKS, CHUNKS + ".bak")
    shutil.copy2(EMB, EMB + ".bak")
    print("backups written: *.bak")

    json.dump(new_chunks, open(CHUNKS, "w", encoding="utf-8"), ensure_ascii=False)
    np.save(EMB, new_emb)
    print("index deduplicated and saved")


if __name__ == "__main__":
    main()
