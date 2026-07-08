"""
app/index_store.py — единственная точка мутаций RAG-индекса (доработка №4).

Все операции под threading.Lock (функции sync — из async кода звать через
asyncio.to_thread) и с атомарной записью (tmp + os.replace): рестарт посреди
записи не оставит битый индекс. Закрывает и прежнюю гонку: два параллельных
ингеста делали read-modify-write файлов без лока (lost update).

Legacy-чанки (исходные 842, без doc_name/folder_id) никогда не матчатся
remove_document — их замена/удаление вне охвата пайплайна.
"""
import json
import os
import threading

import numpy as np

from app.rag import CHUNKS_PATH, EMBEDDINGS_PATH  # единые пути, не дублировать

_lock = threading.Lock()


def load() -> tuple[list[dict], "np.ndarray"]:
    """Чтение индекса с проверкой выравнивания chunks ↔ embeddings."""
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    emb = np.load(EMBEDDINGS_PATH)
    assert len(chunks) == emb.shape[0], (len(chunks), emb.shape)
    return chunks, emb


def _atomic_save(chunks: list[dict], emb: "np.ndarray") -> None:
    assert len(chunks) == emb.shape[0], (len(chunks), emb.shape)
    tmp_json = CHUNKS_PATH + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)
    tmp_npy = EMBEDDINGS_PATH + ".tmp.npy"  # суффикс .npy — np.save не переименует
    np.save(tmp_npy, emb)
    os.replace(tmp_npy, EMBEDDINGS_PATH)
    os.replace(tmp_json, CHUNKS_PATH)


def _matches(chunk: dict, doc_name: str, folder_id: str) -> bool:
    return (chunk.get("doc_name") == doc_name
            and str(chunk.get("folder_id")) == str(folder_id))


def has_document(doc_name: str, folder_id: str) -> bool:
    with _lock:
        with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
            chunks = json.load(f)
    return any(_matches(c, doc_name, folder_id) for c in chunks)


def append_document(new_chunks: list[dict], new_emb: "np.ndarray") -> int:
    """Дописать чанки документа. Возвращает общее число чанков после операции."""
    with _lock:
        chunks, emb = load()
        merged = chunks + list(new_chunks)
        _atomic_save(merged, np.vstack([emb, new_emb]))
        return len(merged)


def remove_document(doc_name: str, folder_id: str) -> int:
    """Удалить чанки документа (точное совпадение doc_name+folder_id).
    Возвращает число удалённых (0 — документа в индексе не было)."""
    with _lock:
        chunks, emb = load()
        keep = [i for i, c in enumerate(chunks)
                if not _matches(c, doc_name, folder_id)]
        removed = len(chunks) - len(keep)
        if removed:
            _atomic_save([chunks[i] for i in keep], emb[keep])
        return removed
