"""
app/doc_parsers.py — парсеры документов для ингеста (доработка №4).

Диспетчер parse_file: docx (scripts/parse_standards), md (перенос из bitrix_bot),
txt (чанки по ~450 слов), pptx (слайд = чанк), pdf (страница = чанк).
Парсеры возвращают «сырые» чанки {text, heading, section} — роли/audience/doc_name
навешивает process_new_document. Импорты форматных библиотек ленивые: бот не должен
падать на старте, если какая-то из них не установлена.
"""
import os
import re
import sys

SUPPORTED_EXTS = ("docx", "md", "pptx", "txt", "pdf")

# Порог «лёгкого» разбора .pptx (09.09): python-pptx поднимает в память ВСЕ части
# пакета, включая видео и картинки, — презентация Welcome.pptx на 735 МБ клала
# контейнер по OOM (сервер 2 ГБ). Выше порога текст читается прямо из zip:
# в память попадают только slideN.xml (десятки КБ), медиа не трогается.
PPTX_LEAN_BYTES = int(float(os.getenv("PPTX_LEAN_MB", "20")) * 1024 * 1024)

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))


def parse_file(path: str, ext: str, file_name: str) -> list[dict]:
    """Диспетчер по расширению. Неизвестный формат → [] (файл пропускается)."""
    if ext == "docx":
        if _SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, _SCRIPTS_DIR)
        from parse_standards import parse_docx  # noqa: PLC0415
        chunks = parse_docx(path)
        # Vision-ингест (21.08): SmartArt-схемы и скриншоты → текстовые чанки
        from app.media_ingest import docx_media_chunks  # noqa: PLC0415
        return chunks + docx_media_chunks(path, file_name)
    if ext == "md":
        return parse_md(path)
    if ext == "txt":
        return parse_txt(path, file_name)
    if ext == "pptx":
        return parse_pptx(path, file_name)
    if ext == "pdf":
        return parse_pdf(path, file_name)
    print(f"[doc_parsers] Unsupported ext: .{ext} ({file_name})")
    return []


def parse_md(filepath: str) -> list[dict]:
    """Minimal .md parser: split by headings. (Перенос из bitrix_bot без изменений.)"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    chunks_out = []
    for section in re.split(r"\n(?=#{1,3} )", content):
        lines = section.strip().splitlines()
        if not lines:
            continue
        m = re.match(r"^#{1,3}\s+(.+)", lines[0])
        heading = m.group(1).strip() if m else "Общее"
        # без заголовка первая строка — это текст, а не заголовок: не терять
        body = " ".join(lines[1:] if m else lines).strip()
        if len(body) > 50:
            chunks_out.append({"text": body, "heading": heading, "section": "Общее"})
    return chunks_out


def parse_txt(path: str, file_name: str) -> list[dict]:
    """Чанки по ~450 слов (конвенция MVP 400–500), heading/section = имя файла."""
    with open(path, "r", encoding="utf-8") as f:
        words = f.read().split()
    out = []
    for i in range(0, len(words), 450):
        body = " ".join(words[i:i + 450])
        if len(body) > 50:
            out.append({"text": body, "heading": file_name, "section": file_name})
    return out


def parse_pptx(path: str, file_name: str) -> list[dict]:
    """Слайд с текстом = чанк; heading = титул слайда или «Слайд N».

    Тяжёлые презентации (> PPTX_LEAN_BYTES) разбираются по zip — см.
    parse_pptx_lean: python-pptx на них съедает память размером с файл."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > PPTX_LEAN_BYTES:
        print(f"[doc_parsers] {file_name}: {size // (1024 * 1024)} МБ — "
              "лёгкий разбор pptx (текст из zip, без медиа)")
        return parse_pptx_lean(path, file_name)

    from pptx import Presentation  # noqa: PLC0415

    prs = Presentation(path)
    out = []
    for n, slide in enumerate(prs.slides, 1):
        # Текст — итерацией a:t по XML слайда (как в parse_pptx_lean):
        # shape.text_frame не видит таблицы и сгруппированные фигуры,
        # и мелкие презентации теряли текст, который тяжёлые сохраняли.
        body = re.sub(r"\s+", " ", " ".join(
            t.text for t in slide.element.iter(f"{_NS_A}t")
            if t.text)).strip()
        if len(body) < 30:  # пустые/мусорные слайды
            continue
        title = ""
        try:
            if slide.shapes.title is not None:
                title = (slide.shapes.title.text or "").strip()
        except (AttributeError, KeyError):
            title = ""
        out.append({"text": body, "heading": title or f"Слайд {n}",
                    "section": file_name})
    return out


_NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _slide_order(zf) -> list[str]:
    """Порядок слайдов из presentation.xml + его rels; фолбэк — числовая
    сортировка ppt/slides/slideN.xml (лексическая дала бы slide10 после slide1)."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    names = set(zf.namelist())
    try:
        rels = ET.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        pres = ET.fromstring(zf.read("ppt/presentation.xml"))
        order = []
        for sld in pres.iter(f"{_NS_P}sldId"):
            target = targets.get(sld.get(f"{_NS_R}id"), "")
            if not target:
                continue
            name = target[1:] if target.startswith("/") else "ppt/" + target
            name = name.replace("ppt/../", "")
            if name in names:
                order.append(name)
        if order:
            return order
    except (KeyError, ET.ParseError) as exc:
        print(f"[doc_parsers] порядок слайдов из rels не прочитан ({exc}) — "
              "фолбэк на имена файлов")

    def _num(name: str) -> int:
        m = re.search(r"slide(\d+)\.xml$", name)
        return int(m.group(1)) if m else 0

    return sorted((n for n in names
                   if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=_num)


def parse_pptx_lean(path: str, file_name: str) -> list[dict]:
    """Тот же контракт, что parse_pptx, но текст берётся прямо из zip.

    Читается только XML слайдов, медиа (видео/картинки) не распаковывается —
    память не зависит от размера презентации. Побочно берёт текст таблиц и
    сгруппированных фигур, до которых shape.text_frame в parse_pptx не достаёт."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    out = []
    with zipfile.ZipFile(path) as zf:
        for n, name in enumerate(_slide_order(zf), 1):
            try:
                root = ET.fromstring(zf.read(name))
            except (KeyError, ET.ParseError) as exc:
                print(f"[doc_parsers] {file_name}: слайд {name} пропущен ({exc})")
                continue

            body = re.sub(r"\s+", " ", " ".join(
                t.text for t in root.iter(f"{_NS_A}t") if t.text)).strip()
            if len(body) < 30:  # пустые/мусорные слайды — как в parse_pptx
                continue

            title = ""
            for sp in root.iter(f"{_NS_P}sp"):
                ph = next((e for e in sp.iter(f"{_NS_P}ph")), None)
                if ph is None or ph.get("type") not in ("title", "ctrTitle"):
                    continue
                title = re.sub(r"\s+", " ", " ".join(
                    t.text for t in sp.iter(f"{_NS_A}t") if t.text)).strip()
                break

            out.append({"text": body, "heading": title or f"Слайд {n}",
                        "section": file_name})
    return out


def parse_pdf(path: str, file_name: str) -> list[dict]:
    """Страница с текстом = чанк. extract_text() у сканов возвращает "" — пропуск."""
    from pypdf import PdfReader  # noqa: PLC0415

    reader = PdfReader(path)
    out = []
    for n, page in enumerate(reader.pages, 1):
        body = re.sub(r"\s+", " ", (page.extract_text() or "")).strip()
        if len(body) < 30:
            if body:  # короткая, но осмысленная страница — не молчать о потере
                print(f"[doc_parsers] {file_name}: стр. {n} пропущена "
                      f"(текст {len(body)} симв. < 30)")
            continue
        out.append({"text": body, "heading": f"Стр. {n}", "section": file_name})
    return out
