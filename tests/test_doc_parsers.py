"""doc_parsers: txt/pptx/pdf/md — без сети."""
import app.doc_parsers as dp


def test_txt_chunking(tmp_path):
    p = tmp_path / "стандарт.txt"
    p.write_text(" ".join(f"слово{i}" for i in range(1000)), encoding="utf-8")
    chunks = dp.parse_txt(str(p), "стандарт.txt")
    sizes = [len(c["text"].split()) for c in chunks]
    assert sizes == [450, 450, 100]
    assert all(c["heading"] == "стандарт.txt" for c in chunks)
    assert all(c["section"] == "стандарт.txt" for c in chunks)


def test_txt_short_tail_dropped(tmp_path):
    p = tmp_path / "к.txt"
    p.write_text("мало слов", encoding="utf-8")  # < 50 символов
    assert dp.parse_txt(str(p), "к.txt") == []


def test_pptx_slides(tmp_path):
    from pptx import Presentation
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # титул + контент
    slide.shapes.title.text = "Заголовок слайда"
    slide.placeholders[1].text = "Достаточно длинный текст тела слайда для чанка."
    prs.slides.add_slide(prs.slide_layouts[6])  # пустой
    path = tmp_path / "презентация.pptx"
    prs.save(str(path))

    chunks = dp.parse_pptx(str(path), "презентация.pptx")
    assert len(chunks) == 1
    assert chunks[0]["heading"] == "Заголовок слайда"
    assert "текст тела" in chunks[0]["text"]
    assert chunks[0]["section"] == "презентация.pptx"


def test_pdf_blank_no_crash(tmp_path):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    path = tmp_path / "пустой.pdf"
    with open(path, "wb") as f:
        w.write(f)
    assert dp.parse_pdf(str(path), "пустой.pdf") == []


def test_md_moved_parser(tmp_path):
    p = tmp_path / "док.md"
    p.write_text("# Один\n" + "текст " * 20 + "\n## Два\nкоротко", encoding="utf-8")
    chunks = dp.parse_md(str(p))
    assert len(chunks) == 1
    assert chunks[0]["heading"] == "Один"


def test_parse_file_dispatch_and_unknown(tmp_path):
    p = tmp_path / "а.txt"
    p.write_text("слово " * 100, encoding="utf-8")
    assert dp.parse_file(str(p), "txt", "а.txt")
    assert dp.parse_file(str(p), "exe", "а.exe") == []
    assert "txt" in dp.SUPPORTED_EXTS and "pptx" in dp.SUPPORTED_EXTS


def _pptx_with_table(path):
    """Презентация: титул + текст + таблица (python-pptx кладёт её в graphicFrame)."""
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Заголовок слайда"
    slide.placeholders[1].text = "Достаточно длинный текст тела слайда для чанка."
    second = prs.slides.add_slide(prs.slide_layouts[6])
    table = second.shapes.add_table(
        1, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "Ячейка с достаточно длинным текстом таблицы"
    table.cell(0, 1).text = "И вторая ячейка с текстом для длины чанка"
    prs.save(str(path))
    return prs


def test_pptx_lean_matches_regular_parser(tmp_path):
    """Лёгкий разбор из zip даёт те же чанки, что python-pptx (первый слайд)."""
    path = tmp_path / "презентация.pptx"
    _pptx_with_table(path)

    lean = dp.parse_pptx_lean(str(path), "презентация.pptx")
    regular = dp.parse_pptx(str(path), "презентация.pptx")  # мелкий файл → python-pptx

    assert lean[0]["heading"] == regular[0]["heading"] == "Заголовок слайда"
    assert lean[0]["text"] == regular[0]["text"]
    assert lean[0]["section"] == "презентация.pptx"
    # Текст таблицы shape.text_frame не отдаёт — лёгкий парсер его видит
    assert len(regular) == 1
    assert len(lean) == 2
    assert "таблицы" in lean[1]["text"]
    assert lean[1]["heading"] == "Слайд 2"


def test_pptx_dispatches_to_lean_above_threshold(tmp_path, monkeypatch):
    """Тяжёлая презентация (> PPTX_LEAN_BYTES) идёт мимо python-pptx: он держит
    в памяти все части пакета, включая видео (OOM на 735 МБ, 09.09)."""
    path = tmp_path / "тяжёлая.pptx"
    _pptx_with_table(path)
    monkeypatch.setattr(dp, "PPTX_LEAN_BYTES", 0)

    def boom(*a, **kw):
        raise AssertionError("python-pptx не должен вызываться для тяжёлого файла")

    monkeypatch.setattr("pptx.Presentation", boom)
    chunks = dp.parse_pptx(str(path), "тяжёлая.pptx")
    assert [c["heading"] for c in chunks] == ["Заголовок слайда", "Слайд 2"]


def test_pptx_lean_slide_order_from_rels(tmp_path):
    """Порядок — из presentation.xml, а не из имён: slide10 не должен идти
    вторым (лексическая сортировка) и не должен теряться."""
    from pptx import Presentation
    prs = Presentation()
    for i in range(1, 12):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = f"Слайд номер {i}"
        slide.placeholders[1].text = f"Текст тела слайда номер {i} для длины чанка."
    path = tmp_path / "много.pptx"
    prs.save(str(path))

    headings = [c["heading"] for c in dp.parse_pptx_lean(str(path), "много.pptx")]
    assert headings == [f"Слайд номер {i}" for i in range(1, 12)]
