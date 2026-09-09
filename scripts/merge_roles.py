#!/usr/bin/env python3
"""
scripts/merge_roles.py — влить конфиг ролей из git в серверный data/roles.json.

Зачем слияние, а не копия: серверный roles.json правит не только репозиторий.
HR-команда «Аббревиатура добавить FIN Финансовая служба» дописывает в него
префиксы и роли прямо в рантайме (app/bitrix_bot.py → save_roles_config), а
deploy.sh до 09.09 копировал файл только при его отсутствии — из-за чего на
боевом сервере до сих пор жил конфиг от 11.08 без роли «Менеджер» и ключа
all_access (HR-роль легаси-аккаунтов молча не применялась).

Правило: добавляем недостающее, НЕ удаляем и НЕ перезаписываем расхождения —
про них печатаем и оставляем решение человеку.

    python3 scripts/merge_roles.py <git-версия> <серверный roles.json>
"""
import json
import os
import shutil
import sys
from datetime import datetime

DICT_SECTIONS = ("roles", "prefixes", "folders", "departments")
LIST_SECTIONS = ("all_access",)


def merge(base: dict, incoming: dict) -> tuple[dict, list[str], list[str]]:
    """Влить incoming (git) в base (сервер).

    Возвращает (слитый конфиг, добавленное, конфликты). base не мутируется."""
    merged = json.loads(json.dumps(base))
    added: list[str] = []
    conflicts: list[str] = []

    for section in DICT_SECTIONS:
        src = incoming.get(section) or {}
        if not isinstance(src, dict):
            continue
        dst = merged.setdefault(section, {})
        for key, value in src.items():
            if key not in dst:
                dst[key] = value
                added.append(f"{section}.{key}")
            elif dst[key] != value:
                conflicts.append(
                    f"{section}.{key}: сервер={dst[key]!r} git={value!r}"
                )

    for section in LIST_SECTIONS:
        src = incoming.get(section) or []
        if not isinstance(src, list):
            continue
        dst = merged.setdefault(section, [])
        for value in src:
            if value not in dst:
                dst.append(value)
                added.append(f"{section}.{value}")

    # Комментарии-подсказки в конфиге: только если их на сервере ещё нет
    for key, value in incoming.items():
        if key.startswith("_") and key not in merged:
            merged[key] = value
            added.append(key)

    return merged, added, conflicts


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    git_path, target_path = argv[1], argv[2]

    incoming = _load(git_path)
    if not os.path.exists(target_path):
        # Первый деплой: серверного конфига ещё нет — кладём гитовый как есть
        shutil.copyfile(git_path, target_path)
        print(f"[roles] {target_path} создан из {git_path}")
        return 0

    base = _load(target_path)
    merged, added, conflicts = merge(base, incoming)

    for line in conflicts:
        print(f"[roles] ⚠️ расхождение (оставлено значение сервера): {line}")

    if not added:
        print("[roles] изменений нет")
        return 0

    backup = f"{target_path}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copyfile(target_path, backup)
    tmp = target_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target_path)
    print(f"[roles] добавлено: {', '.join(added)}")
    print(f"[roles] бэкап: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
