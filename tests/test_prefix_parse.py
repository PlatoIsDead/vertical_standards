"""№11: разбор префиксов департаментов в имени файла (чистая функция, без сети)."""
import json

import pytest

import app.roles as roles

PREFIX_CFG = {
    "roles": {
        "housekeeper": "Горничная / Уборщица",
        "admin_reception": "Администратор ресепшн (СПиР)",
        "engineer": "Техник / Инженер",
        "reservations": "Отдел бронирования",
        "sales": "Отдел продаж",
        "all_staff": "Все сотрудники",
    },
    "prefixes": {
        "FO": "admin_reception", "HSKP": "housekeeper", "ENG": "engineer",
        "RES": "reservations", "SAL": "sales", "ALL": "all_staff",
    },
    "folders": {},
}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "roles.json"
    p.write_text(json.dumps(PREFIX_CFG, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(p))


def test_single_prefix(cfg):
    parsed = roles.parse_filename("FO Внешний вид сотрудника.docx")
    assert parsed["roles"] == ["admin_reception"]
    assert parsed["title"] == "Внешний вид сотрудника"
    assert parsed["suspicious"] is None


def test_multi_prefix_commas_in_title(cfg):
    parsed = roles.parse_filename(
        "FO, HSKP, ENG, RES Вывод номера из обращения (ремонт, OOS, ООО).docx")
    assert parsed["roles"] == ["admin_reception", "housekeeper", "engineer",
                               "reservations"]
    assert parsed["title"] == "Вывод номера из обращения (ремонт, OOS, ООО)"
    assert parsed["suspicious"] is None      # OOS/ООО — часть названия, не роли


def test_all_prefix(cfg):
    parsed = roles.parse_filename("ALL КОНФЕДЕНЦИАЛЬНОСТЬ.docx")
    assert parsed["roles"] == ["all_staff"]
    assert parsed["title"] == "КОНФЕДЕНЦИАЛЬНОСТЬ"


def test_lowercase_and_no_space(cfg):
    parsed = roles.parse_filename("fo,res Алгоритм.docx")
    assert parsed["roles"] == ["admin_reception", "reservations"]
    assert parsed["title"] == "Алгоритм"


def test_no_prefix_returns_none(cfg):
    parsed = roles.parse_filename("Договор.docx")
    assert parsed["roles"] is None            # ≠ ["all_staff"]: фолбэк у вызывающего
    assert parsed["title"] == "Договор"
    assert parsed["suspicious"] is None


def test_caps_cyrillic_title_not_suspicious(cfg):
    parsed = roles.parse_filename(
        "ENG ДЕЙСТВИЯ ДЕЖУРНОГО СМЕНЫ ИНЖЕНЕРНОЙ СЛУЖБЫ.docx")
    assert parsed["roles"] == ["engineer"]
    assert parsed["suspicious"] is None       # капс-кириллица — норма названий


def test_unknown_latin_abbrev_suspicious(cfg):
    parsed = roles.parse_filename("FO, XYZ Документ.docx")
    assert parsed["roles"] == ["admin_reception"]
    assert parsed["suspicious"] == "XYZ"
    assert parsed["title"] == "XYZ Документ"


def test_duplicate_prefix_deduped(cfg):
    parsed = roles.parse_filename("FO, FO Дубль.docx")
    assert parsed["roles"] == ["admin_reception"]
    assert parsed["title"] == "Дубль"


def test_empty_prefixes_config(cfg, tmp_path, monkeypatch):
    p = tmp_path / "no_prefixes.json"
    p.write_text(json.dumps({"roles": {}, "folders": {}}), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(p))
    parsed = roles.parse_filename("FO Внешний вид.docx")
    assert parsed["roles"] is None            # без блока prefixes = как раньше


def test_display_name(cfg):
    assert roles.display_name("Стандарты.docx") == "Стандарты"
    assert roles.display_name(
        "FO, RES АЛГОРИТМ БРОНИРОВАНИЯ ПО ТЕЛЕФОНУ.docx") == \
        "АЛГОРИТМ БРОНИРОВАНИЯ ПО ТЕЛЕФОНУ"
