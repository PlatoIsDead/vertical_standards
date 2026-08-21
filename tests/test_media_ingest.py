"""Vision-ингест (PRPs/vision-ingest.md): SmartArt-линеаризация на РЕАЛЬНОЙ
боевой фикстуре, кэш описаний, извлечение медиа из docx, eval_vision — офлайн
(LLM мокается)."""
import base64
import json
import os
import sys
import zipfile

import app.media_ingest as mi

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "smartart_data1.xml")
# валидный 1×1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


# ── SmartArt на боевой схеме ─────────────────────────────────────────────────

def test_smartart_outline_real_fixture():
    xml = open(FIXTURE, "rb").read()
    out = mi.smartart_outline(xml)
    lines = out.splitlines()
    # порядок стрелок: звонок раньше приветствия, приветствие раньше уточнения
    idx = {s: i for i, s in enumerate(lines)}
    call = next(i for i, s in enumerate(lines) if "Входящий звонок" in s)
    hello = next(i for i, s in enumerate(lines) if "Приветсвие гостя" in s)
    clarify = next(i for i, s in enumerate(lines) if "Уточнить" in s)
    assert call < hello < clarify
    # ветвление «другой отель сети» с вариантами ИЛИ
    assert any("другой отель сети" in s for s in lines)
    assert sum(1 for s in lines if s.strip("— ").startswith("ИЛИ")) == 3
    # все содержательные тексты узлов присутствуют
    for key in ("создать бронирование", "Отправить подтверждение",
                "ЗАСЕЛЕНИЕ", "невозвратного тарифа"):
        assert any(key in s for s in lines), key
    assert idx  # noqa: у outline стабильная структура


def test_smartart_to_text_llm_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(mi, "CACHE_PATH", str(tmp_path / "cache.json"))

    def boom(*a, **kw):
        raise ValueError("нет сети")

    monkeypatch.setattr(mi, "_llm_text", boom)
    xml = open(FIXTURE, "rb").read()
    out = mi.smartart_to_text(xml, "Тест")
    assert "Входящий звонок" in out          # сырой outline вместо падения


# ── Кэш описаний ─────────────────────────────────────────────────────────────

def test_describe_image_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mi, "CACHE_PATH", str(tmp_path / "cache.json"))
    calls = []

    def fake_llm(system, user, max_tokens=2000, content_extra=None):
        calls.append(1)
        return "ОПИСАНИЕ КАРТИНКИ"

    monkeypatch.setattr(mi, "_llm_text", fake_llm)
    assert mi.describe_image(PNG, "Док") == "ОПИСАНИЕ КАРТИНКИ"
    assert mi.describe_image(PNG, "Док") == "ОПИСАНИЕ КАРТИНКИ"
    assert len(calls) == 1                                  # второй — из кэша
    cache = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert list(cache.values()) == ["ОПИСАНИЕ КАРТИНКИ"]    # переживает процесс


# ── Извлечение и чанки ───────────────────────────────────────────────────────

def _make_docx(path, with_emf=False):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w/>")
        z.writestr("word/media/image1.png", PNG)
        z.writestr("word/media/image2.png", PNG + b"2")
        if with_emf:
            z.writestr("word/media/image3.emf", b"emf-bytes")
        z.writestr("word/diagrams/data1.xml",
                   open(FIXTURE, "rb").read())


def test_extract_media_and_chunks(tmp_path, monkeypatch):
    docx = tmp_path / "FO Тест.docx"
    _make_docx(str(docx), with_emf=True)
    diagrams, images = mi.extract_media(str(docx))
    assert len(diagrams) == 1 and len(images) == 2          # EMF пропущен

    monkeypatch.setattr(mi, "smartart_to_text",
                        lambda xml, ctx: "АЛГОРИТМ ИЗ СХЕМЫ")
    monkeypatch.setattr(mi, "describe_image",
                        lambda img, ctx: "ОПИСАНИЕ СКРИНА")
    chunks = mi.docx_media_chunks(str(docx), "FO Тест.docx")
    assert [c["text"] for c in chunks] == [
        "[Схема] АЛГОРИТМ ИЗ СХЕМЫ",
        "[Скриншот 1] ОПИСАНИЕ СКРИНА",
        "[Скриншот 2] ОПИСАНИЕ СКРИНА"]
    assert chunks[0]["heading"] == "Схема: Тест"            # без префикса FO
    assert chunks[1]["section"] == "FO Тест.docx"


def test_media_chunks_partial_failures(tmp_path, monkeypatch):
    docx = tmp_path / "FO Тест.docx"
    _make_docx(str(docx))
    monkeypatch.setattr(mi, "smartart_to_text",
                        lambda xml, ctx: "СХЕМА")
    calls = {"n": 0}

    def flaky(img, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("vision упал")
        return "ВТОРОЙ СКРИН"

    monkeypatch.setattr(mi, "describe_image", flaky)
    chunks = mi.docx_media_chunks(str(docx), "FO Тест.docx")
    assert [c["text"] for c in chunks] == [
        "[Схема] СХЕМА", "[Скриншот 2] ВТОРОЙ СКРИН"]       # 1-й пропущен


def test_parse_file_docx_includes_media(tmp_path, monkeypatch):
    import app.doc_parsers as dp
    sys.path.insert(0, dp._SCRIPTS_DIR)
    import parse_standards
    docx = tmp_path / "FO Тест.docx"
    _make_docx(str(docx))                    # минимальный zip — не для python-docx
    monkeypatch.setattr(parse_standards, "parse_docx",
                        lambda path: [{"text": "обычный текст " * 5,
                                       "heading": "h", "section": "s"}])
    monkeypatch.setattr(mi, "docx_media_chunks",
                        lambda path, name: [{"text": "[Схема] X",
                                             "heading": "Схема: Тест",
                                             "section": name}])
    chunks = dp.parse_file(str(docx), "docx", "FO Тест.docx")
    assert len(chunks) == 2 and chunks[-1]["text"] == "[Схема] X"


# ── eval_vision (моки) ───────────────────────────────────────────────────────

def test_eval_vision_report(tmp_path, monkeypatch):
    import eval_vision as ev
    import numpy as np

    docx = tmp_path / "FO Тест.docx"
    _make_docx(str(docx))
    monkeypatch.setattr(ev, "_build_index", lambda paths: (
        [{"text": "[Схема] АЛГ", "roles": ["admin_reception"],
          "audience": "staff", "doc_name": "FO Тест.docx",
          "heading": "h", "section": "s"}],
        np.zeros((1, 4), dtype=np.float32)))
    verdicts = iter(["correct", "correct", "correct", "correct",
                     "partial", "wrong"])

    def fake_vision(client, system, user, image, validate=None):
        if "questions" in system or "Составь" in system:
            return {"questions": ["В1?", "В2?"]}
        return {"verdict": next(verdicts), "reason": "ок"}

    monkeypatch.setattr(ev, "_vision_json", fake_vision)
    monkeypatch.setattr(ev, "rag_answer",
                        lambda **kw: ("ОТВЕТ", []))
    monkeypatch.setattr(ev, "OpenAI", lambda **kw: object())
    monkeypatch.setattr(sys, "argv", [
        "eval_vision", "--date", "20990101", "--questions", "2",
        "--files", str(docx), "--out", str(tmp_path)])
    ev.main()
    report = (tmp_path / "eval_vision_20990101.md").read_text(
        encoding="utf-8")
    assert "Схема 1" in report and "Скриншот 2" in report
    assert "66% correct" in report                  # 4 из 6, 1 wrong
    assert "ПОРОГ НЕ ПРОЙДЕН" in report


