"""
scripts/reingest.py — принудительный переингест документов (vision-ингест
21.08: старые файлы обработаны без медиа). Снимает строки processed_files и
чанки документа из индекса — поллер переингестит в течение цикла.

    docker exec vertical-standards-bot python scripts/reingest.py --doc "АЛГОРИТМ"
    docker exec vertical-standards-bot python scripts/reingest.py --doc "Негарантированные"
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import app.db as db  # noqa: E402
import app.index_store as index_store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Переингест документа")
    ap.add_argument("--doc", required=True,
                    help="подстрока имени документа (регистрозависимо)")
    args = ap.parse_args()

    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT file_id, doc_name, folder_id FROM processed_files")]
    hits = [r for r in rows if args.doc in r["doc_name"]]
    if not hits:
        raise SystemExit(f"Нет processed-файлов с «{args.doc}» в имени.")
    for r in hits:
        removed = index_store.remove_document(r["doc_name"],
                                              str(r["folder_id"] or ""))
        db.remove_processed_file(str(r["file_id"]))
        print(f"reingest: {r['doc_name']!r} (file {r['file_id']}) — "
              f"-{removed} чанков, строка снята")
    print("Поллер переингестит в течение POLL_INTERVAL_SEC "
          "(курс/вопросы сохраняются — дедуп по doc_name).")


if __name__ == "__main__":
    main()
