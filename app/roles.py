"""
app/roles.py — реестр ролей и ролевая фильтрация RAG.

Источник правды: data/roles.json
  roles:   {role_id: "Русское имя"} — "all_staff" служебная, в меню выбора не показывается
  folders: {bitrix_folder_id: [role_id, ...]} — роль документа определяется папкой

Конфиг читается с диска при каждом вызове (файл крошечный) — можно менять
без рестарта, поллер подхватит на следующем цикле.
"""
import json
import os

import numpy as np

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "roles.json")

ALL_STAFF = "all_staff"


def load_roles_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"[roles] config unavailable ({exc!r}) — ролевая фильтрация отключена")
        return {"roles": {}, "folders": {}}
    return cfg


def selectable_roles() -> list[tuple[str, str]]:
    """Роли для меню выбора сотрудником, в порядке из конфига (без all_staff)."""
    roles = load_roles_config().get("roles", {})
    return [(rid, name) for rid, name in roles.items() if rid != ALL_STAFF]


def role_name(role_id: str) -> str:
    return load_roles_config().get("roles", {}).get(role_id, role_id)


def roles_for_folder(folder_id: str) -> list[str]:
    return list(load_roles_config().get("folders", {}).get(str(folder_id), []))


def role_mask(chunks: list[dict], role_id: str | None) -> np.ndarray:
    """0/1-маска по ролям для умножения на scores (паттерн section_filter в rag.py).

    - audience=guest исключается ВСЕГДА (бот отвечает только сотрудникам)
    - role_id=None (роль не выбрана / legacy-сессия) — пропускаем всё негостевое
    - чанк без ключа roles / с пустым roles = all_staff (обратная совместимость)
    """
    vals = []
    for c in chunks:
        if c.get("audience") == "guest":
            vals.append(0.0)
            continue
        roles = c.get("roles") or []
        ok = role_id is None or not roles or ALL_STAFF in roles or role_id in roles
        vals.append(1.0 if ok else 0.0)
    return np.array(vals, dtype=np.float32)
