"""
app/roles.py — реестр ролей и ролевая фильтрация RAG.

Источник правды: data/roles.json
  roles:    {role_id: "Русское имя"} — "all_staff" служебная, в меню выбора не показывается
  prefixes: {"FO": role_id, ...} — аббревиатуры департаментов в начале имени файла
            («FO, RES АЛГОРИТМ....docx», формат клиента №11); роли документа из имени
  folders:  {bitrix_folder_id: [role_id, ...]} — legacy №1, фолбэк без префиксов в имени

Конфиг читается с диска при каждом вызове (файл крошечный) — можно менять
без рестарта, поллер подхватит на следующем цикле.
"""
import json
import os
import re

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


def save_roles_config(cfg: dict) -> None:
    """Атомарная запись roles.json (19.08: HR-команда «Аббревиатура …»).
    Файл читают на каждый запрос и правят руками — tmp+replace, читаемый JSON."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def selectable_roles() -> list[tuple[str, str]]:
    """Роли для меню выбора сотрудником, в порядке из конфига (без all_staff)."""
    roles = load_roles_config().get("roles", {})
    return [(rid, name) for rid, name in roles.items() if rid != ALL_STAFF]


def role_name(role_id: str) -> str:
    return load_roles_config().get("roles", {}).get(role_id, role_id)


def roles_for_folder(folder_id: str) -> list[str]:
    return list(load_roles_config().get("folders", {}).get(str(folder_id), []))


def role_for_departments(dep_ids: list) -> str | None:
    """№8: роль по отделам Битрикса (roles.json departments, ключи-строки).
    Все отделы юзера мапятся в ОДНУ роль → она; в разные / ни одного
    замапленного / пустой маппинг → None (фолбэк: память сессии или меню)."""
    cfg = load_roles_config().get("departments", {})
    found = {cfg.get(str(d)) for d in dep_ids} - {None}
    return found.pop() if len(found) == 1 else None


# Кандидат в опечатку префикса: ТОЛЬКО латиница — названия у клиента бывают
# целиком капсом кириллицей («ENG ДЕЙСТВИЯ ДЕЖУРНОГО...»), их не тревожить.
_SUSPICIOUS_RE = re.compile(r"^[A-Z]{2,6}$")


def parse_filename(file_name: str) -> dict:
    """Разбор префиксов департаментов в имени файла (№11).

    «FO, HSKP, ENG, RES Вывод номера из обращения (ремонт, OOS, ООО).docx» →
    роли по реестру prefixes. Разбор идёт по токенам через запятую и
    ОСТАНАВЛИВАЕТСЯ на первом токене, который не префикс (взяв его первое
    слово, если оно префикс) — запятые внутри названия не ломают разбор.

    Возвращает {"roles": list[str] | None, "title": str, "suspicious": str | None}:
      roles None — известных префиксов нет вовсе (≠ ["all_staff"], фолбэк
        решает вызывающий);
      title — название без префиксов (только для показа сотруднику,
        идентичность документа — всегда полное имя файла);
      suspicious — латинская аббревиатура сразу после известных префиксов,
        которой нет в реестре (вероятная опечатка — сообщить HR).
    """
    prefixes = load_roles_config().get("prefixes", {})
    stem = (file_name.rsplit(".", 1)[0] if "." in file_name else file_name).strip()
    found: list[str] = []
    title, suspicious = stem, None
    if prefixes:
        parts = stem.split(",")
        for i, part in enumerate(parts):
            token = part.strip()
            if token.upper() in prefixes:            # чистый токен: «FO»
                found.append(prefixes[token.upper()])
                title = ",".join(parts[i + 1:]).strip()
                continue
            head, _, rest = token.partition(" ")
            if head.upper() in prefixes:             # хвост: «RES Вывод (ремонт»
                found.append(prefixes[head.upper()])
                title = ",".join([rest] + parts[i + 1:]).strip()
            else:
                title = ",".join(parts[i:]).strip()
                if found and _SUSPICIOUS_RE.match(head):
                    suspicious = head
            break
    roles = list(dict.fromkeys(found)) or None
    return {"roles": roles, "title": title if roles else stem,
            "suspicious": suspicious}


def display_name(file_name: str) -> str:
    """Название документа для показа сотруднику — без префиксов департаментов."""
    return parse_filename(file_name)["title"] or file_name


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
