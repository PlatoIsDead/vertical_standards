"""
scripts/backfill_roles.py — одноразовый бэкфилл ролей и audience в существующий индекс.

Живой индекс (data/chunks_cache.json) содержит чанки только с {heading, section, text}.
Скрипт добавляет каждому чанку:
  roles    — переиспользуя существующую логику:
             * русские секции («Хозяйственная служба», …) → assign_roles
               из scripts/generate_role_docs.py
             * VA.*-коды / GENERAL → get_roles из scripts/parse_standards.py
  audience — "guest" если заголовок адресован гостю (и нет маркеров персонала),
             иначе "staff". Гостевые чанки RAG никогда не отдаёт сотрудникам.

Текст не меняется → переэмбеддинг НЕ нужен; порядок и число чанков сохраняются
(выравнивание с embeddings_cache.npy), пишутся бэкапы *.bak.

Запуск (из корня репо, venv проекта):
    python scripts/backfill_roles.py --dry-run   # статистика, ничего не пишет
    python scripts/backfill_roles.py             # бэкап + запись
"""
import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_role_docs import SECTION_DEFAULT_ROLES, assign_roles  # noqa: E402
from parse_standards import get_roles  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
CHUNKS = os.path.join(DATA, "chunks_cache.json")
EMB = os.path.join(DATA, "embeddings_cache.npy")

# Явные маркеры разделов, АДРЕСОВАННЫХ гостю (инструкция постояльцу).
# Простое «гост*» в заголовке не работает: «ПРИЛОЖЕНИЕ ГОСТЯ», «РАБОТА С ГОСТЯМИ…» —
# инструкции ДЛЯ ПЕРСОНАЛА про гостей, а реальный гостевой раздел
# («ДЕЙСТВИЯ В СЛУЧАЕ ПОЖАРА, ЕСЛИ ВЫ НАХОДИТЕСЬ В НОМЕРЕ») слова «гость» не содержит.
# Precision > recall; список пополняется по итогам созвонов.
_GUEST_HEADING_MARKERS = (
    "если вы находитесь в номере",
    "памятка гост",
    "памятка для гост",
    "инструкция для гост",
    "правила проживания для гост",
)

# гость/гостя/гостям… но НЕ гостиница/гостиничный — для FYI-списка на ручную проверку
_GUEST_MENTION_RE = re.compile(r"гост(?!ин)[а-яё]*", re.IGNORECASE)


def detect_audience(heading: str) -> str:
    h = heading.lower()
    return "guest" if any(m in h for m in _GUEST_HEADING_MARKERS) else "staff"


def assign(chunk: dict) -> list[str]:
    section = chunk.get("section", "Общее")
    heading = chunk.get("heading", "")
    if section in SECTION_DEFAULT_ROLES:
        return sorted(set(assign_roles(chunk)))
    # VA.FO / VA.HSK / GENERAL и т.п. — префиксная логика parse_standards
    return sorted(set(get_roles(section, heading.lower())))


def enrich(chunks: list[dict]) -> dict:
    """Добавляет roles/audience чанкам, у которых их нет. Возвращает статистику."""
    role_counts: Counter = Counter()
    audience_counts: Counter = Counter()
    guest_headings: list[str] = []
    mention_headings: list[str] = []

    for c in chunks:
        heading = c.get("heading", "")
        if not c.get("roles"):
            c["roles"] = assign(c)
        if "audience" not in c:
            c["audience"] = detect_audience(heading)
        if c["audience"] == "guest":
            guest_headings.append(heading)
        elif _GUEST_MENTION_RE.search(heading.lower()):
            mention_headings.append(heading)
        for r in c["roles"]:
            role_counts[r] += 1
        audience_counts[c["audience"]] += 1

    return {
        "roles": role_counts,
        "audience": audience_counts,
        "guest_headings": guest_headings,
        "mention_headings": mention_headings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкфилл ролей и audience в индекс")
    parser.add_argument("--dry-run", action="store_true",
                        help="только статистика, файл не пишется")
    args = parser.parse_args()

    chunks = json.load(open(CHUNKS, encoding="utf-8"))
    emb = np.load(EMB)
    assert len(chunks) == emb.shape[0], (len(chunks), emb.shape)
    n_before = len(chunks)
    print(f"индекс: {n_before} чанков, emb {emb.shape}")

    stats = enrich(chunks)

    print("\nРаспределение по ролям:")
    for role, count in stats["roles"].most_common():
        print(f"  {role}: {count}")
    print(f"\naudience: {dict(stats['audience'])}")

    print(f"\nГостевые чанки ({len(stats['guest_headings'])}) — проверить глазами:")
    for h in sorted(set(stats["guest_headings"])):
        print(f"  [guest] {h}")
    if stats["mention_headings"]:
        print("\nУпоминают гостей, но помечены staff (FYI для созвона):")
        for h in sorted(set(stats["mention_headings"])):
            print(f"  [staff] {h}")

    assert len(chunks) == n_before, "порядок/число чанков изменились — стоп"

    if args.dry_run:
        print("\n--dry-run: ничего не записано")
        return

    shutil.copy2(CHUNKS, CHUNKS + ".bak")
    print(f"\nбэкап: {CHUNKS}.bak")
    with open(CHUNKS, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)
    print(f"записано: {CHUNKS} (embeddings не тронуты)")


if __name__ == "__main__":
    main()
