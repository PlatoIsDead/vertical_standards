"""backfill_roles — назначение ролей/audience, сохранность индекса."""
import backfill_roles as bf


def test_russian_section_gets_section_roles():
    chunk = {"section": "Хозяйственная служба",
             "heading": "АЛГОРИТМ УБОРКИ НОМЕРА", "text": "..."}
    roles = bf.assign(chunk)
    assert "housekeeper" in roles


def test_va_section_uses_prefix_mapping():
    chunk = {"section": "VA.FO", "heading": "ОБЯЗАННОСТИ АДМИНИСТРАТОРА СПИР",
             "text": "..."}
    roles = bf.assign(chunk)
    assert "admin_reception" in roles


def test_emergency_section_is_all_staff():
    chunk = {"section": "Чрезвычайные ситуации",
             "heading": "ДЕЙСТВИЯ ПЕРСОНАЛА ПРИ ПОЖАРЕ", "text": "..."}
    assert bf.assign(chunk) == ["all_staff"]


def test_guest_instruction_detected():
    assert bf.detect_audience(
        "ДЕЙСТВИЯ В СЛУЧАЕ ПОЖАРА, ЕСЛИ ВЫ НАХОДИТЕСЬ В НОМЕРЕ") == "guest"


def test_staff_docs_about_guests_stay_staff():
    # «про гостей» ≠ «для гостей»
    for heading in ("ПРИЛОЖЕНИЕ ГОСТЯ",
                    "РАБОТА С ГОСТЯМИ СОБСТВЕННИКОВ НЕ В УПРАВЛЕНИИ УК",
                    "ДЕЙСТВИЯ АДМИНИСТРАТОРА ПРИ НЕВЫЕЗДЕ ГОСТЯ"):
        assert bf.detect_audience(heading) == "staff"


def test_enrich_preserves_order_and_text_and_is_idempotent():
    chunks = [
        {"section": "Хозяйственная служба", "heading": "УБОРКА", "text": "раз"},
        {"section": "Общее", "heading": "ПАМЯТКА ГОСТЮ", "text": "два"},
    ]
    bf.enrich(chunks)
    assert [c["text"] for c in chunks] == ["раз", "два"]
    assert all("roles" in c and "audience" in c for c in chunks)
    assert chunks[1]["audience"] == "guest"

    before = [dict(c) for c in chunks]
    bf.enrich(chunks)  # повторный прогон ничего не меняет
    assert chunks == before
