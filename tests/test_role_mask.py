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
