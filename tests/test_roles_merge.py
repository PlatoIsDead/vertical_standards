"""scripts/merge_roles.py — слияние гитового roles.json в серверный.

Серверный конфиг правит не только репозиторий: HR из бота добавляет
аббревиатуры и роли, поэтому деплой обязан доливать недостающее, ничего не
теряя и не перезаписывая расхождения молча.
"""
import json
import os
import sys

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import merge_roles as mr  # noqa: E402

SERVER = {
    "roles": {"housekeeper": "Горничная", "fin": "Финансовая служба"},
    "prefixes": {"HSKP": "housekeeper", "FIN": "fin"},   # FIN добавил HR из бота
    "folders": {},
    "departments": {},
}
GIT = {
    "_comment": "источник правды",
    "all_access": ["manager"],
    "roles": {"manager": "Менеджер (доступ ко всем стандартам)",
              "housekeeper": "Горничная"},
    "prefixes": {"HSKP": "housekeeper"},
    "folders": {},
    "departments": {},
}


def test_adds_missing_role_and_all_access():
    merged, added, conflicts = mr.merge(SERVER, GIT)
    assert merged["roles"]["manager"] == "Менеджер (доступ ко всем стандартам)"
    assert merged["all_access"] == ["manager"]
    assert set(added) == {"roles.manager", "all_access.manager", "_comment"}
    assert conflicts == []


def test_keeps_server_side_additions():
    """Аббревиатура и роль, добавленные HR через бота, переживают слияние."""
    merged, _, _ = mr.merge(SERVER, GIT)
    assert merged["prefixes"]["FIN"] == "fin"
    assert merged["roles"]["fin"] == "Финансовая служба"


def test_conflict_reported_not_overwritten():
    git = json.loads(json.dumps(GIT))
    git["roles"]["housekeeper"] = "Сотрудник хозслужбы"
    merged, added, conflicts = mr.merge(SERVER, git)
    assert merged["roles"]["housekeeper"] == "Горничная"      # сервер не тронут
    assert any("roles.housekeeper" in c for c in conflicts)
    assert "roles.housekeeper" not in added


def test_merge_does_not_mutate_input():
    before = json.loads(json.dumps(SERVER))
    mr.merge(SERVER, GIT)
    assert SERVER == before


def test_all_access_union_keeps_order():
    server = {"all_access": ["manager"]}
    git = {"all_access": ["manager", "gm"]}
    merged, added, _ = mr.merge(server, git)
    assert merged["all_access"] == ["manager", "gm"]
    assert added == ["all_access.gm"]


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def test_cli_writes_backup_then_second_run_is_noop(tmp_path, capsys):
    git_path, target = tmp_path / "git.json", tmp_path / "roles.json"
    _write(git_path, GIT)
    _write(target, SERVER)

    assert mr.main(["merge_roles.py", str(git_path), str(target)]) == 0
    assert "добавлено: roles.manager" in capsys.readouterr().out
    backups = list(tmp_path.glob("roles.json.bak-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == SERVER
    assert json.loads(target.read_text(encoding="utf-8"))["roles"]["manager"]

    assert mr.main(["merge_roles.py", str(git_path), str(target)]) == 0
    assert "изменений нет" in capsys.readouterr().out
    assert len(list(tmp_path.glob("roles.json.bak-*"))) == 1   # второй бэкап не нужен


def test_cli_creates_target_on_first_deploy(tmp_path, capsys):
    git_path, target = tmp_path / "git.json", tmp_path / "roles.json"
    _write(git_path, GIT)
    assert mr.main(["merge_roles.py", str(git_path), str(target)]) == 0
    assert "создан из" in capsys.readouterr().out
    assert json.loads(target.read_text(encoding="utf-8")) == GIT
