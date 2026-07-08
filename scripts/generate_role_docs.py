"""
scripts/generate_role_docs.py

Читает chunks_cache.json, присваивает каждому чанку одну или несколько ролей,
генерирует отдельный .md файл для каждой роли.

Запуск: python scripts/generate_role_docs.py
"""

import json
import os
import re
from collections import defaultdict

DATA_DIR   = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "role_docs")
CHUNKS_PATH = os.path.join(DATA_DIR, "chunks_cache.json")

# ─── РОЛИ И ИХ КЛЮЧЕВЫЕ СЛОВА ────────────────────────────────────────────────
# Формат: "ID роли": ("Название", [ключевые слова в тексте/заголовке])

ROLES = {
    "general_manager": (
        "Генеральный менеджер / Управляющий",
        ["генеральн", "управляющий", "управляющего", "генерального менеджера",
         "руководство объект", "директор отел"]
    ),
    "admin_reception": (
        "Администратор ресепшн (СПиР)",
        ["администратор", "спир", "служба приема", "службы приема",
         "ресепшн", "reception", "фронт", "va.fo"]
    ),
    "supervisor_fo": (
        "Супервайзер СПиР",
        ["супервайзер спир", "супервайзера спир", "va.fo.05",
         "ответственный руководитель спир", "va.fo.06"]
    ),
    "housekeeper": (
        "Горничная / Уборщик",
        ["горничн", "уборщиц", "уборщик", "housekeeping", "хаускипинг",
         "уборка номер", "уборки номер", "уборку номер",
         "бельев", "белье", "смена белья", "генеральная уборка",
         "va.hsk.3", "va.hsk.5", "va.hsk.6"]
    ),
    "hsk_supervisor": (
        "Супервайзер / Руководитель хозяйственной службы",
        ["руководитель хозяйственной", "супервайзер хозяйственной",
         "супервайзера хозяйственной", "va.hsk.01", "va.hsk.02",
         "va.hsk.03", "va.hsk.04", "va.hsk.1", "va.hsk.2"]
    ),
    "engineer": (
        "Инженер / Технический персонал",
        ["инженер", "инженерн", "технич", "техник", "сантехник",
         "электрик", "va.eng", "ooo", "oos", "out of order",
         "out of service", "kaizen", "maintenance"]
    ),
    "hr_manager": (
        "HR менеджер / Менеджер по персоналу",
        ["hr", "кадр", "персонал", "подбор", "обучени", "оценк",
         "va.hr", "сотрудник отдела кадр"]
    ),
    "owner_relations": (
        "Менеджер по работе с собственниками",
        ["собственник", "инвестор", "va.ow", "личный кабинет инвестор",
         "дебиторск", "выход из управления"]
    ),
    "sales_manager": (
        "Менеджер по продажам",
        ["менеджер по продажам", "отдел продаж", "va.sd",
         "бронирование", "долгосрочн", "корпоратив", "event"]
    ),
    "marketing": (
        "Менеджер по маркетингу / Продвижению",
        ["маркетинг", "продвижени", "va.md", "smm", "контент",
         "блогер", "seo", "реклам", "полиграфия"]
    ),
    "security": (
        "Служба безопасности",
        ["безопасност", "охран", "скуд", "пропускной",
         "служба безопасности", "сотрудник безопасности"]
    ),
    "all_staff": (
        "Все сотрудники",
        ["все сотрудник", "каждый сотрудник", "персонал отел",
         "сотрудники отел", "пожар", "эвакуац", "чрезвычайн",
         "минировани", "террорист", "вооруженн", "внешний вид",
         "конфиденциальн", "антитеррорист"]
    ),
}

# Привязка разделов документа к ролям (по умолчанию)
SECTION_DEFAULT_ROLES = {
    "Служба приёма и размещения": ["admin_reception", "supervisor_fo"],
    "Хозяйственная служба":       ["housekeeper", "hsk_supervisor"],
    "Управление персоналом":      ["hr_manager", "general_manager"],
    "Работа с собственниками":    ["owner_relations", "general_manager"],
    "Отдел продаж":               ["sales_manager", "general_manager"],
    "Маркетинг и продвижение":    ["marketing", "general_manager"],
    "Чрезвычайные ситуации":      ["all_staff"],
    "Административная политика":  ["general_manager", "all_staff"],
    "Общее":                      ["all_staff"],
}


def assign_roles(chunk: dict) -> list[str]:
    """
    1. Базовые роли из раздела (SECTION_DEFAULT_ROLES)
    2. Уточнение по заголовку — конкретные должности
    3. ЧС -> all_staff
    """
    heading = chunk.get("heading", "").lower()
    section = chunk.get("section", "Общее")

    matched = set(SECTION_DEFAULT_ROLES.get(section, ["all_staff"]))

    heading_role_map = [
        (["va.fo.04", "обязанности администратора"], "admin_reception"),
        (["va.fo.05", "обязанности супервайзера спир"], "supervisor_fo"),
        (["va.fo.06", "обязанности ответственного руководителя спир"], "supervisor_fo"),
        (["алгоритм уборки", "va.hsk.5", "va.hsk.6",
          "горничн", "генеральная уборка", "dnd", "не беспокоить"], "housekeeper"),
        (["va.hsk.01", "va.hsk.02", "va.hsk.03", "va.hsk.04",
          "руководитель хозяйственной", "супервайзер хозяйственной"], "hsk_supervisor"),
        (["va.ow.04"], "owner_relations"),
        (["va.ow.05", "менеджера по работе с инвесторами"], "owner_relations"),
        (["va.sd.05", "va.sd.06", "va.sd.07", "va.sd.08", "va.sd.09",
          "менеджера по продажам", "директора по продажам"], "sales_manager"),
        (["va.md.05", "va.md.06", "менеджера по продвижению"], "marketing"),
        (["va.hr.0.0.1", "va.hr.0.0.2", "va.hr.0.0.3", "va.hr.0.0.4"], "hr_manager"),
        (["инженерн", "главный инженер", "va.eng"], "engineer"),
        (["действия всех сотрудников", "действия персонала",
          "пожар", "эвакуац", "минировани", "террорист"], "all_staff"),
        (["генерального менеджера", "управляющего отелем"], "general_manager"),
    ]

    for keywords, role_id in heading_role_map:
        for kw in keywords:
            if kw in heading:
                matched.add(role_id)
                if role_id not in ("all_staff", "general_manager"):
                    matched.discard("all_staff")
                break

    if section == "Чрезвычайные ситуации":
        matched = {"all_staff"}

    return list(matched)


def load_chunks() -> list[dict]:
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_markdown(role_id: str, role_name: str, chunks: list[dict]) -> str:
    """Генерирует markdown документ для роли."""
    lines = [
        f"# {role_name}",
        "",
        "*Стандарты и инструкции — апарт-отели Вертикаль*",
        "",
        f"Всего правил в этом документе: **{len(chunks)}**",
        "",
        "---",
        "",
    ]

    # Группируем по разделу
    by_section = defaultdict(list)
    for chunk in chunks:
        by_section[chunk["section"]].append(chunk)

    for section, section_chunks in sorted(by_section.items()):
        lines.append(f"## {section}")
        lines.append("")

        # Группируем по заголовку
        by_heading = defaultdict(list)
        for chunk in section_chunks:
            by_heading[chunk["heading"]].append(chunk)

        for heading, heading_chunks in by_heading.items():
            if heading and heading != "Общее":
                lines.append(f"### {heading}")
                lines.append("")
            for chunk in heading_chunks:
                # Чистим текст от мусора
                text = chunk["text"].strip()
                text = re.sub(r'\s+', ' ', text)
                lines.append(text)
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main():
    print("=== Генерация документов по ролям ===")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    chunks = load_chunks()
    print(f"Загружено чанков: {len(chunks)}")

    # Присваиваем роли каждому чанку
    role_chunks = defaultdict(list)
    for chunk in chunks:
        roles = assign_roles(chunk)
        chunk["roles"] = roles
        for role_id in roles:
            role_chunks[role_id].append(chunk)

    # Статистика
    print("\nРаспределение по ролям:")
    for role_id, (role_name, _) in ROLES.items():
        count = len(role_chunks[role_id])
        print(f"  {role_name}: {count} правил")

    # Сохраняем chunks с ролями
    enriched_path = os.path.join(DATA_DIR, "chunks_with_roles.json")
    with open(enriched_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"\nОбновлённые чанки → {enriched_path}")

    # Генерируем markdown для каждой роли
    print(f"\nГенерирую документы в {OUTPUT_DIR}/")
    for role_id, (role_name, _) in ROLES.items():
        if not role_chunks[role_id]:
            continue
        md = generate_markdown(role_id, role_name, role_chunks[role_id])
        filename = f"{role_id}.md"
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"  ✓ {filename} ({len(role_chunks[role_id])} правил)")

    # Сводный индекс
    index_lines = ["# Индекс документов по ролям\n",
                   "*Апарт-отели Вертикаль — Стандарты*\n", "---\n"]
    for role_id, (role_name, _) in ROLES.items():
        count = len(role_chunks[role_id])
        if count > 0:
            index_lines.append(f"- [{role_name}](./{role_id}.md) — {count} правил")
    index_path = os.path.join(OUTPUT_DIR, "INDEX.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines))
    print("\n  ✓ INDEX.md")
    print("\nГотово! Открой папку: role_docs/")


if __name__ == "__main__":
    main()
