"""role_mask — чистая ролевая маска (без сети)."""
from app.roles import role_mask

CHUNKS = [
    {"text": "t0", "roles": ["housekeeper"], "audience": "staff"},        # 0
    {"text": "t1", "roles": ["admin_reception"], "audience": "staff"},    # 1
    {"text": "t2", "roles": ["all_staff"], "audience": "staff"},          # 2
    {"text": "t3", "audience": "staff"},                                  # 3 нет roles = all_staff
    {"text": "t4", "roles": [], "audience": "staff"},                     # 4 пустой = all_staff
    {"text": "t5", "roles": ["all_staff"], "audience": "guest"},          # 5 гостевой
]


def test_matching_role_passes():
    mask = role_mask(CHUNKS, "housekeeper")
    assert mask[0] == 1.0


def test_foreign_role_blocked():
    mask = role_mask(CHUNKS, "housekeeper")
    assert mask[1] == 0.0


def test_all_staff_passes_for_any_role():
    mask = role_mask(CHUNKS, "engineer")
    assert mask[2] == 1.0


def test_missing_or_empty_roles_treated_as_all_staff():
    mask = role_mask(CHUNKS, "housekeeper")
    assert mask[3] == 1.0
    assert mask[4] == 1.0


def test_guest_always_blocked():
    for role in ("housekeeper", "admin_reception", None):
        assert role_mask(CHUNKS, role)[5] == 0.0


def test_role_none_passes_everything_except_guest():
    mask = role_mask(CHUNKS, None)
    assert list(mask) == [1.0, 1.0, 1.0, 1.0, 1.0, 0.0]


# ── 02.09: роль «Менеджер» (all_access) видит всё ────────────────────────────

def _manager_cfg(tmp_path, monkeypatch):
    import json
    import app.roles as roles_mod
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"manager": "Менеджер", "housekeeper": "Горничная",
                  "admin_reception": "СПиР", "all_staff": "Все"},
        "all_access": ["manager"],
        "prefixes": {"HSKP": "housekeeper", "FO": "admin_reception"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles_mod, "CONFIG_PATH", str(cfg))


def test_manager_sees_every_department_chunk(tmp_path, monkeypatch):
    _manager_cfg(tmp_path, monkeypatch)
    mask = role_mask(CHUNKS, "manager")
    assert list(mask) == [1.0, 1.0, 1.0, 1.0, 1.0, 0.0]   # всё, кроме гостевого


def test_role_matches_rules(tmp_path, monkeypatch):
    from app.roles import role_matches, role_sees_all
    _manager_cfg(tmp_path, monkeypatch)
    assert role_sees_all("manager") and not role_sees_all("housekeeper")
    assert role_matches("manager", ["admin_reception"])       # чужой отдел
    assert role_matches("manager", [])                        # без ролей
    assert not role_matches("housekeeper", ["admin_reception"])
    assert role_matches("housekeeper", ["all_staff"])
    assert role_matches(None, ["all_staff"])                  # роли нет — общее
    assert not role_matches(None, ["housekeeper"])


def test_all_access_absent_keeps_old_behaviour(tmp_path, monkeypatch):
    """Конфиг без ключа all_access — прежняя логика (клиентские правки рукой)."""
    import json
    import app.roles as roles_mod
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"manager": "Менеджер", "housekeeper": "Горничная"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles_mod, "CONFIG_PATH", str(cfg))
    assert role_mask(CHUNKS, "manager")[0] == 0.0            # чужой отдел закрыт
