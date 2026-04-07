"""
scripts/parse_standards.py

Парсит франчайзинговый стандарт .docx → chunks_cache.json
Каждый чанк = один подраздел документа (## VA.XX.YY ...)

Запуск:
    python scripts/parse_standards.py --input data/Vertical_franchise_standards.docx

Зависимости: pip install python-docx
"""

import json
import re
import os
import argparse
from docx import Document

# ─── МАППИНГ: префикс раздела → роли ────────────────────────────────────────

SECTION_ROLES = {
    "VA.ADM":    ["general_manager", "all_staff"],
    "VA.ADM.3":  ["all_staff"],          # ЧС — все сотрудники
    "VA.HR":     ["hr_manager", "general_manager"],
    "VA.FO":     ["admin_reception", "supervisor_fo"],
    "VA.OW":     ["owner_relations", "general_manager"],
    "VA.SD":     ["sales_manager", "general_manager"],
    "VA.MD":     ["marketing", "general_manager"],
    "VA.HSK":    ["housekeeper", "hsk_supervisor"],
    "VA.ENG":    ["engineer"],
}

# Уточнение ролей по ключевым словам в заголовке
HEADING_ROLE_OVERRIDES = [
    (["va.fo.04", "обязанности администратора"],           "admin_reception"),
    (["va.fo.05", "обязанности супервайзера спир"],        "supervisor_fo"),
    (["va.fo.06", "обязанности ответственного руководителя"], "supervisor_fo"),
    (["va.hsk.5", "va.hsk.6", "алгоритм уборки",
      "горничн", "генеральная уборка", "dnd"],             "housekeeper"),
    (["va.hsk.01", "va.hsk.02", "va.hsk.03", "va.hsk.04",
      "руководитель хозяйственной"],                       "hsk_supervisor"),
    (["va.ow"],                                            "owner_relations"),
    (["va.sd.05", "va.sd.06", "va.sd.07",
      "менеджера по продажам"],                             "sales_manager"),
    (["va.md.05", "va.md.06",
      "менеджера по продвижению"],                          "marketing"),
    (["va.hr.0.0"],                                        "hr_manager"),
    (["va.eng", "инженер", "технич"],                      "engineer"),
    (["пожар", "эвакуац", "минировани",
      "террорист", "вооружен"],                             "all_staff"),
    (["генерального менеджера", "управляющего"],            "general_manager"),
]


def get_roles(section_code: str, heading_lower: str) -> list[str]:
    """Определяет роли для чанка по разделу и заголовку."""
    # 1. Базовые роли по префиксу раздела (самый длинный совпадающий префикс)
    roles = set()
    best = ""
    for prefix, role_list in SECTION_ROLES.items():
        if section_code.upper().startswith(prefix) and len(prefix) > len(best):
            best = prefix
            roles = set(role_list)

    if not roles:
        roles = {"all_staff"}

    # 2. Уточнение по заголовку
    for keywords, role_id in HEADING_ROLE_OVERRIDES:
        for kw in keywords:
            if kw in heading_lower:
                roles.add(role_id)
                # Если нашли конкретную роль — убираем "все сотрудники" (кроме ЧС)
                if role_id not in ("all_staff", "general_manager"):
                    roles.discard("all_staff")
                break

    return sorted(roles)


def extract_section_code(text: str) -> str:
    """
    Извлекает код раздела из заголовка.
    'VA.FO.04 ОБЯЗАННОСТИ АДМИНИСТРАТОРА' → 'VA.FO.04'
    """
    m = re.match(r'(VA\.[A-Z0-9]+(?:\.[0-9]+)*)', text.upper())
    return m.group(1) if m else "GENERAL"


def get_parent_section(code: str) -> str:
    """VA.FO.04 → VA.FO"""
    parts = code.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else code


def parse_docx(filepath: str) -> list[dict]:
    """
    Парсит .docx и возвращает список чанков.
    Каждый чанк:
    {
        "id":       уникальный int,
        "section":  "VA.FO",           # родительский раздел
        "code":     "VA.FO.04",        # код этого подраздела
        "heading":  "ОБЯЗАННОСТИ АДМИНИСТРАТОРА СПИР",
        "text":     "полный текст...",
        "roles":    ["admin_reception", "supervisor_fo"],
        "status":   null               # null / "green" / "red" / "yellow"
    }
    """
    doc = Document(filepath)
    chunks = []
    chunk_id = 0

    current_heading = "ОБЩИЕ ПОЛОЖЕНИЯ"
    current_code = "GENERAL"
    current_text_lines = []

    def flush(heading, code, lines):
        """Сохраняет накопленный текст как чанк."""
        nonlocal chunk_id
        text = " ".join(lines).strip()
        text = re.sub(r'\s+', ' ', text)
        if len(text) < 30:   # пропускаем пустые/мусорные чанки
            return
        section = get_parent_section(code)
        chunk = {
            "id":      chunk_id,
            "section": section,
            "code":    code,
            "heading": heading,
            "text":    text,
            "roles":   get_roles(code, heading.lower()),
            "status":  None
        }
        chunks.append(chunk)
        chunk_id += 1

    for para in doc.paragraphs:
        style = para.style.name  # e.g. 'Heading 1', 'Heading 2', 'Normal'
        text = para.text.strip()

        if not text:
            continue

        is_heading = style.startswith("Heading")

        if is_heading:
            # Сохраняем предыдущий чанк
            flush(current_heading, current_code, current_text_lines)
            current_text_lines = []

            # Новый заголовок
            code = extract_section_code(text)
            heading_clean = re.sub(r'^VA\.[A-Z0-9.]+\s*', '', text).strip()
            if not heading_clean:
                heading_clean = text

            current_heading = heading_clean.upper()
            current_code = code

        else:
            current_text_lines.append(text)

    # Последний чанк
    flush(current_heading, current_code, current_text_lines)

    return chunks


def main():
    parser = argparse.ArgumentParser(description="Парсит стандарты → chunks_cache.json")
    parser.add_argument("--input",  required=True, help="Путь к .docx файлу")
    parser.add_argument("--output", default="data/chunks_cache.json",
                        help="Куда сохранить (default: data/chunks_cache.json)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Файл не найден: {args.input}")
        return

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    print(f"Парсим: {args.input}")
    chunks = parse_docx(args.input)
    print(f"Найдено чанков: {len(chunks)}")

    # Статистика по ролям
    from collections import Counter
    role_counts = Counter()
    for c in chunks:
        for r in c["roles"]:
            role_counts[r] += 1
    print("\nРаспределение по ролям:")
    for role, count in sorted(role_counts.items(), key=lambda x: -x[1]):
        print(f"  {role}: {count}")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено → {args.output}")


if __name__ == "__main__":
    main()
