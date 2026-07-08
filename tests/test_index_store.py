"""index_store: append/remove/has, выравнивание, legacy-чанки нетронуты."""
import json

import numpy as np
import pytest

import app.index_store as store


@pytest.fixture
def idx(tmp_path, monkeypatch):
    chunks = [
        {"text": "легаси-чанк", "heading": "h", "section": "s"},  # без doc_name
        {"text": "старый текст", "heading": "h", "section": "s",
         "doc_name": "Док.docx", "folder_id": "111"},
    ]
    cp = tmp_path / "chunks.json"
    ep = tmp_path / "emb.npy"
    cp.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    np.save(str(ep), np.zeros((2, 4), dtype=np.float32))
    monkeypatch.setattr(store, "CHUNKS_PATH", str(cp))
    monkeypatch.setattr(store, "EMBEDDINGS_PATH", str(ep))
    return cp, ep


def test_append_keeps_alignment(idx):
    new = [{"text": "новый", "heading": "h", "section": "s",
            "doc_name": "Новый.txt", "folder_id": "222"}]
    total = store.append_document(new, np.ones((1, 4), dtype=np.float32))
    assert total == 3
    chunks, emb = store.load()
    assert len(chunks) == emb.shape[0] == 3
    assert store.has_document("Новый.txt", "222")


def test_remove_targets_only_matching(idx):
    removed = store.remove_document("Док.docx", "111")
    assert removed == 1
    chunks, emb = store.load()
    assert len(chunks) == emb.shape[0] == 1
    assert chunks[0]["text"] == "легаси-чанк"  # legacy нетронут


def test_remove_wrong_folder_removes_nothing(idx):
    assert store.remove_document("Док.docx", "999") == 0
    assert store.remove_document("Нет.docx", "111") == 0
    chunks, _ = store.load()
    assert len(chunks) == 2


def test_files_valid_after_operations(idx):
    cp, ep = idx
    store.append_document(
        [{"text": "т", "heading": "h", "section": "s",
          "doc_name": "Д.txt", "folder_id": "1"}],
        np.zeros((1, 4), dtype=np.float32),
    )
    store.remove_document("Д.txt", "1")
    # файлы перечитываются штатно, tmp-огрызков не осталось
    chunks = json.loads(cp.read_text(encoding="utf-8"))
    emb = np.load(str(ep))
    assert len(chunks) == emb.shape[0] == 2
    assert not list(cp.parent.glob("*.tmp*"))
