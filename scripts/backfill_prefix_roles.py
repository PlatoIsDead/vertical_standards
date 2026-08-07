"""
scripts/backfill_prefix_roles.py — одноразовая миграция индекса под №11 (префиксы).

1) Перетегирует roles уже заингещенных чанков по префиксам doc_name
   («FO, RES АЛГОРИТМ....docx» → роли из data/roles.json prefixes): файлы могли
   заингеститься ДО ввода префиксов (рекурсия №4 ходила в подпапки с all_staff).
2) Вычищает мусорные чанки macOS (doc_name начинается с «.»: AppleDouble ._*.docx,
   .DS_Store) + их строки processed_files; курсы таких doc_name архивирует.

Текст живых чанков не меняется → переэмбеддинг НЕ нужен. Удаление мусора
фильтрует chunks и embeddings СИНХРОННО (выравнивание сохраняется), пишутся
бэкапы *.bak. Legacy-чанки без doc_name (исходные 842) не трогаются.

Запуск (из корня репо, venv проекта):
    python scripts/backfill_prefix_roles.py --dry-run   # статистика, ничего не пишет
    python scripts/backfill_prefix_roles.py             # бэкапы + запись
После боевого прогона — рестарт uvicorn (индекс читается на старте).
"""
import argparse
import json
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db import _conn, init_db  # noqa: E402
from app.roles import parse_filename  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
CHUNKS = os.path.join(DATA, "chunks_cache.json")
EMB = os.path.join(DATA, "embeddings_cache.npy")


def migrate(chunks: list[dict]) -> tuple[list[int], int]:
    """(индексы живых чанков, число перетеганных). Мутирует чанки на месте."""
    keep, retagged = [], 0
    for i, c in enumerate(chunks):
        name = c.get("doc_name")
        if name and name.startswith("."):
            continue                      # мусор — в удаление
        keep.append(i)
        if not name:
            continue                      # legacy 842 без doc_name — не трогаем
        parsed = parse_filename(name)
        if parsed["roles"] and c.get("roles") != parsed["roles"]:
            c["roles"] = parsed["roles"]
            retagged += 1
    return keep, retagged


def junk_db_rows() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT file_id, doc_name FROM processed_files WHERE doc_name LIKE '.%'"
        ).fetchall()
        return [dict(r) for r in rows]


def purge_junk_db(rows: list[dict]) -> int:
    """Снять мусорные строки processed_files, архивировать мусорные курсы."""
    with _conn() as conn:
        for r in rows:
            conn.execute("DELETE FROM processed_files WHERE file_id = ?",
                         (r["file_id"],))
        cur = conn.execute(
            "UPDATE courses SET archived_at = datetime('now')"
            " WHERE doc_name LIKE '.%' AND archived_at IS NULL"
        )
        return cur.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Перетег ролей по префиксам имени + чистка macOS-мусора")
    parser.add_argument("--dry-run", action="store_true",
                        help="только статистика, ничего не пишется")
    args = parser.parse_args()

    chunks = json.load(open(CHUNKS, encoding="utf-8"))
    emb = np.load(EMB)
    assert len(chunks) == emb.shape[0], (len(chunks), emb.shape)
    print(f"индекс: {len(chunks)} чанков, emb {emb.shape}")

    keep, retagged = migrate(chunks)
    junk = len(chunks) - len(keep)
    init_db()
    junk_rows = junk_db_rows()
    print(f"перетегано по префиксам: {retagged}")
    print(f"мусорных чанков (._*/.DS_Store): {junk}")
    print(f"мусорных строк processed_files: {len(junk_rows)}"
          + (f" ({[r['doc_name'] for r in junk_rows]})" if junk_rows else ""))

    if args.dry_run:
        print("\n--dry-run: ничего не записано")
        return

    if retagged or junk:
        shutil.copy2(CHUNKS, CHUNKS + ".bak")
        shutil.copy2(EMB, EMB + ".bak")
        kept_chunks = [chunks[i] for i in keep]
        kept_emb = emb[keep]
        assert len(kept_chunks) == kept_emb.shape[0]
        with open(CHUNKS, "w", encoding="utf-8") as f:
            json.dump(kept_chunks, f, ensure_ascii=False)
        np.save(EMB, kept_emb)
        print(f"записано: {len(kept_chunks)} чанков (бэкапы *.bak)")
    else:
        print("индекс не менялся")

    if junk_rows:
        archived = purge_junk_db(junk_rows)
        print(f"processed_files: -{len(junk_rows)} строк; "
              f"курсов архивировано: {archived}")


if __name__ == "__main__":
    main()
