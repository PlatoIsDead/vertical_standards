"""
scripts/dedup_courses.py — разовая чистка курсов-дублей (гонка параллельного
ингеста копий 29.07: «КОНФЕДЕНЦИАЛЬНОСТЬ» ×4 и др.). По умолчанию DRY-RUN —
печатает план; применить: --apply.

Правило: на каждый doc_name остаётся один незаархивированный курс — тот, на
который ссылаются сессии, иначе с минимальным id; остальные archived_at=now.

Запуск на сервере (БД в контейнере):
    docker exec vertical-standards-bot python scripts/dedup_courses.py
    docker exec vertical-standards-bot python scripts/dedup_courses.py --apply
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import app.db as db  # noqa: E402  (DB_PATH env-override уважается)


def main() -> None:
    parser = argparse.ArgumentParser(description="Чистка курсов-дублей")
    parser.add_argument("--apply", action="store_true",
                        help="применить (без флага — dry-run)")
    args = parser.parse_args()

    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, doc_name FROM courses WHERE archived_at IS NULL"
            " ORDER BY id")]
        with_sessions = {r[0] for r in conn.execute(
            "SELECT DISTINCT course_id FROM sessions")}

    groups: dict[str, list[int]] = {}
    for r in rows:
        groups.setdefault(r["doc_name"], []).append(r["id"])

    plan: list[tuple[int, str, int]] = []
    for name, ids in groups.items():
        if len(ids) < 2:
            continue
        keep = next((i for i in ids if i in with_sessions), min(ids))
        plan += [(i, name, keep) for i in ids if i != keep]

    if not plan:
        print("Дублей нет.")
        return
    for cid, name, keep in plan:
        print(f"архив: курс {cid} «{name}» (остаётся {keep})")
    if not args.apply:
        print(f"\nDRY-RUN: {len(plan)} курсов к архиву. Применить: --apply")
        return
    now = datetime.utcnow().isoformat()
    with db._conn() as conn:
        for cid, _name, _keep in plan:
            conn.execute("UPDATE courses SET archived_at = ? WHERE id = ?",
                         (now, cid))
    print(f"Заархивировано: {len(plan)}")


if __name__ == "__main__":
    main()
