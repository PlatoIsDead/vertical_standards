"""
app/media_ingest.py — визуальный контент docx в текст индекса (PRP vision-ingest).

Два класса контента (исследование 21.08 на боевых файлах):
1. SmartArt-диаграммы (word/diagrams/data*.xml) — флоучарты со СТРЕЛКАМИ,
   лежащими в XML явно (dgm:cxn srcId→destId): порядок и ветвления
   восстанавливаются ДЕТЕРМИНИРОВАННО, vision не нужен и не используется.
2. Скриншоты (word/media/*.png|jpg) с рукописными аннотациями (стрелки к
   колонкам, обводки, цифры шагов) — gpt-5.5 vision с промптом, требующим
   транскрибировать аннотации и задаваемый ими ПОРЯДОК ДЕЙСТВИЙ.

Кэш описаний по sha256 байтов (data/vision_cache.json): реингест и копии
не платят повторно. Смена промпта = ручной сброс кэша (см.
PRPs/vision-ingest-sprints.md).
"""
import base64
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile

from dotenv import load_dotenv
from openai import OpenAI

from app.roles import display_name

load_dotenv()

CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                          "vision_cache.json")
MAX_IMAGE_BYTES = 3_000_000
_IMG_RE = re.compile(r"word/media/[^/]+\.(png|jpe?g)$", re.IGNORECASE)
_DGM_RE = re.compile(r"word/diagrams/data\d+\.xml$")

_NS = {
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}

DESCRIBE_PROMPT = """Ты расшифровываешь изображение из рабочей инструкции апарт-отеля «Вертикаль»
(документ «{ctx}»). Ответ строго из четырёх секций, в этом порядке:

1) ЭКРАН: какая система и какой это экран/окно.
2) СТРУКТУРА — полная транскрипция того, что видно:
   - для таблиц: ВСЕ заголовки колонок слева направо, затем примечательные
     строки ДОСЛОВНО (значения через « | »);
   - для меню/навигации: пункты сверху вниз, отметь выделенный;
   - для форм: подписи полей и их значения.
3) АННОТАЦИИ: каждая рукописная/цветная пометка (стрелка, рамка, обводка,
   цифра, буквы) отдельной строкой: номер/цвет/форма → на какой ТОЧНЫЙ
   элемент указывает (колонка + значение / кнопка / пункт меню).
4) ПОРЯДОК ДЕЙСТВИЙ: что аннотации предписывают сделать — нумерованные шаги.

Не пропускай элементы: если текст не читается — пиши «неразборчиво».
НИЧЕГО не выдумывай и не додумывай. Язык: русский."""

SMARTART_PROMPT = """Ниже — структура схемы-алгоритма из стандарта «{ctx}»: отступы «— » = вложенность,
порядок строк = порядок шагов (восстановлен из стрелок схемы). Перепиши её связным
пошаговым алгоритмом: нумерация шагов, ветвления как «Если …, то …».
Ничего не добавляй от себя, порядок и формулировки шагов не меняй."""


# ── Кэш ──────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE_PATH)


# ── SmartArt: детерминированная линеаризация стрелок ─────────────────────────

def smartart_outline(xml_bytes: bytes) -> str:
    """dgm:pt (узлы с текстом) + dgm:cxn parOf (стрелки иерархии, сортировка
    по srcOrd) → outline с отступами. Pres/транзитные узлы пропускаются,
    дети бестекстовых узлов поднимаются к текстовому предку."""
    root = ET.fromstring(xml_bytes)
    texts: dict[str, str] = {}
    node_ids: set[str] = set()
    for pt in root.findall(".//dgm:ptLst/dgm:pt", _NS):
        mid = pt.get("modelId")
        if pt.get("type") not in (None, "doc", "asst"):
            continue  # pres/parTrans/sibTrans — не содержание
        node_ids.add(mid)
        t = " ".join(x.text.strip() for x in pt.findall(".//a:t", _NS)
                     if x.text and x.text.strip())
        if t:
            texts[mid] = " ".join(t.split())

    children: dict[str, list] = {}
    has_parent: set[str] = set()
    for cxn in root.findall(".//dgm:cxnLst/dgm:cxn", _NS):
        if cxn.get("type") not in (None, "parOf"):
            continue
        src, dest = cxn.get("srcId"), cxn.get("destId")
        if src not in node_ids or dest not in node_ids:
            continue
        children.setdefault(src, []).append(
            (int(cxn.get("srcOrd", "0")), dest))
        has_parent.add(dest)

    lines: list[str] = []

    def walk(mid: str, depth: int) -> None:
        has_text = mid in texts
        if has_text:
            lines.append("— " * depth + texts[mid])
        for _order, child in sorted(children.get(mid, []),
                                    key=lambda x: x[0]):
            walk(child, depth + (1 if has_text else 0))

    roots = [m for m in node_ids if m not in has_parent]
    for r in roots:
        walk(r, 0)
    return "\n".join(lines)


def _llm_text(system: str, user: str, max_tokens: int = 2000,
              content_extra: list | None = None) -> str:
    """Свободнотекстовый вызов gpt-5.5 (описания — не JSON): конвенции
    max_completion_tokens / без temperature. content_extra — vision-части."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                    timeout=90.0, max_retries=2)
    content: list = [{"type": "text", "text": user}]
    if content_extra:
        content += content_extra
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": content}],
        max_completion_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def smartart_to_text(xml_bytes: bytes, doc_context: str) -> str:
    """Outline схемы → связный алгоритм (LLM). Сбой LLM → сырой outline:
    точный порядок важнее гладкости."""
    outline = smartart_outline(xml_bytes)
    if not outline.strip():
        return ""
    cache = _load_cache()
    key = hashlib.sha256(xml_bytes).hexdigest()
    if key in cache:
        return cache[key]
    try:
        text = _llm_text(SMARTART_PROMPT.format(ctx=doc_context), outline)
    except Exception as exc:
        print(f"[media] smartart LLM failed ({exc!r}) — отдаю outline")
        return outline
    if not text:
        return outline
    cache[key] = text
    _save_cache(cache)
    return text


def describe_image(image_bytes: bytes, doc_context: str) -> str:
    """Скриншот → текст-фрагмент инструкции (vision, кэш по sha256)."""
    cache = _load_cache()
    key = hashlib.sha256(image_bytes).hexdigest()
    if key in cache:
        return cache[key]
    b64 = base64.b64encode(image_bytes).decode()
    text = _llm_text(
        DESCRIBE_PROMPT.format(ctx=doc_context),
        "Расшифруй изображение по инструкции из системного сообщения.",
        max_tokens=3000,   # Sprint 2: полная транскрипция структуры длиннее
        content_extra=[{"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}",
                                      "detail": "high"}}],   # мелкий текст таблиц
    )
    if text:
        cache[key] = text
        _save_cache(cache)
    return text


# ── Извлечение и чанки ───────────────────────────────────────────────────────

def extract_media(path: str) -> tuple[list[bytes], list[bytes]]:
    """(диаграммы data*.xml, картинки png/jpg) в порядке имён файлов.
    Не-изображения (EMF/WMF) и гиганты — скип с логом."""
    diagrams: list[bytes] = []
    images: list[bytes] = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for n in sorted(n for n in names if _DGM_RE.match(n)):
            diagrams.append(z.read(n))
        media = [n for n in names if n.startswith("word/media/")]
        for n in sorted(media,
                        key=lambda s: [int(x) if x.isdigit() else x
                                       for x in re.split(r"(\d+)", s)]):
            if not _IMG_RE.match(n):
                print(f"[media] skip (не png/jpg): {n}")
                continue
            data = z.read(n)
            if len(data) > MAX_IMAGE_BYTES:
                print(f"[media] skip (>{MAX_IMAGE_BYTES}Б): {n}")
                continue
            images.append(data)
    return diagrams, images


def docx_media_chunks(path: str, file_name: str) -> list[dict]:
    """Медиа-чанки docx для parse_file. Сбой на элементе — лог и дальше
    (частичный результат лучше пустого); системный сбой → []."""
    try:
        diagrams, images = extract_media(path)
    except Exception as exc:
        print(f"[media] {file_name}: extract failed {exc!r}")
        return []
    ctx = display_name(file_name)
    chunks: list[dict] = []
    for d in diagrams:
        try:
            text = smartart_to_text(d, ctx)
        except Exception as exc:
            print(f"[media] {file_name}: schema failed {exc!r}")
            continue
        if text.strip():
            chunks.append({"text": "[Схема] " + text,
                           "heading": f"Схема: {ctx}",
                           "section": file_name})
    for i, img in enumerate(images, 1):
        try:
            text = describe_image(img, ctx)
        except Exception as exc:
            print(f"[media] {file_name}: image {i} failed {exc!r}")
            continue
        if text.strip():
            chunks.append({"text": f"[Скриншот {i}] " + text,
                           "heading": f"Скриншот {i}: {ctx}",
                           "section": file_name})
    if chunks:
        print(f"[media] {file_name}: +{len(chunks)} медиа-чанков")
    return chunks
