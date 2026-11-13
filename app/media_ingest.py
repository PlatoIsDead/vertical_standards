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
не платят повторно. Ключ включает версию распознавания и локальный контекст (см.
PRPs/vision-ingest-sprints.md).
"""
import base64
import hashlib
import json
import os
import re
import posixpath
import xml.etree.ElementTree as ET
import zipfile

from dotenv import load_dotenv
from openai import OpenAI

from app.roles import display_name

load_dotenv()

CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                          "vision_cache.json")
VISION_CACHE_VERSION = "source-context-v2"
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
Контекст документа помогает найти подпись, но не доказывает содержимое картинки.
Не переноси детали других изображений. Если шаг или направление не видны,
пиши «не установлено». НИЧЕГО не выдумывай и не додумывай. Язык: русский."""

SMARTART_PROMPT = """Ниже — структура схемы-алгоритма из стандарта «{ctx}»: отступы «— » = вложенность,
порядок строк = порядок шагов (восстановлен из стрелок схемы). Перепиши её связным
пошаговым алгоритмом: нумерация шагов, ветвления как «Если …, то …».
Ничего не добавляй от себя, порядок и формулировки шагов не меняй."""


# ── Кэш ──────────────────────────────────────────────────────────────────────

def vision_cache_key(data: bytes, context: str, kind: str) -> str:
    payload = VISION_CACHE_VERSION.encode() + kind.encode() + context.encode() + b"\0" + data
    return VISION_CACHE_VERSION + ":" + hashlib.sha256(payload).hexdigest()


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
    doc_order: list[str] = []  # порядок pt в ptLst — детерминированный обход
    for pt in root.findall(".//dgm:ptLst/dgm:pt", _NS):
        mid = pt.get("modelId")
        if pt.get("type") not in (None, "doc", "asst"):
            continue  # pres/parTrans/sibTrans — не содержание
        if mid in node_ids:
            continue  # дубликат modelId в битых файлах — берём первый
        node_ids.add(mid)
        doc_order.append(mid)
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
        try:
            order = int(cxn.get("srcOrd", "0"))
        except ValueError:
            order = 0  # битый srcOrd: stable sort сохранит порядок cxnLst
        children.setdefault(src, []).append((order, dest))
        has_parent.add(dest)

    lines: list[str] = []
    visited: set[str] = set()  # цикл/несколько родителей в битой схеме —
    # без защиты walk уходил в RecursionError и схема терялась целиком

    def walk(mid: str, depth: int) -> None:
        if mid in visited:
            return
        visited.add(mid)
        has_text = mid in texts
        if has_text:
            lines.append("— " * depth + texts[mid])
        for _order, child in sorted(children.get(mid, []),
                                    key=lambda x: x[0]):
            walk(child, depth + (1 if has_text else 0))

    # Корней может быть несколько (битый/чужой файл): порядок множества
    # node_ids зависит от PYTHONHASHSEED — обходим в порядке документа.
    # Это НЕ «истинный порядок шагов»: parOf — иерархия, а не стрелки.
    for r in doc_order:
        if r not in has_parent:
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
        model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": content}],
        max_completion_tokens=max_tokens,
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        # Sprint 3: reasoning-модель может сжечь ВЕСЬ бюджет на невидимый
        # reasoning и вернуть пустой content — молчать нельзя
        raise RuntimeError(
            f"пустой content (finish_reason="
            f"{resp.choices[0].finish_reason}, budget={max_tokens})")
    return text


def smartart_to_text(xml_bytes: bytes, doc_context: str) -> str:
    """Outline схемы → связный алгоритм (LLM). Сбой LLM → сырой outline:
    точный порядок важнее гладкости."""
    outline = smartart_outline(xml_bytes)
    if not outline.strip():
        return ""
    cache = _load_cache()
    key = vision_cache_key(xml_bytes, doc_context, "smartart")
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
    return outline + "\n\nОписание: " + text


def describe_image(image_bytes: bytes, doc_context: str) -> str:
    """Скриншот → текст-фрагмент инструкции (vision, кэш по sha256)."""
    cache = _load_cache()
    key = vision_cache_key(image_bytes, doc_context, "image")
    if key in cache:
        return cache[key]
    b64 = base64.b64encode(image_bytes).decode()
    extra = [{"type": "image_url",
              "image_url": {"url": f"data:image/png;base64,{b64}",
                            "detail": "high"}}]   # мелкий текст таблиц
    text = ""
    # Sprint 3: reasoning может сжечь бюджет → пустой content; повтор с ×2
    for budget in (8000, 16000):
        try:
            text = _llm_text(
                DESCRIBE_PROMPT.format(ctx=doc_context),
                "Расшифруй изображение по инструкции из системного сообщения.",
                max_tokens=budget, content_extra=extra)
            break
        except RuntimeError as exc:
            print(f"[media] describe (budget={budget}): {exc}")
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


def media_occurrences(path: str) -> list[dict]:
    """Document order and nearby paragraphs, resolved through OOXML relationships."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        rels = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        targets = {r.get("Id"): posixpath.normpath(posixpath.join("word", r.get("Target", "")))
                   for r in rels if r.get("TargetMode") != "External"}
        paragraphs = list(root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"))
        texts = [" ".join(t.text or "" for t in p.iter()
                          if t.tag.endswith("}t")) for p in paragraphs]
        result = []
        for position, paragraph in enumerate(paragraphs):
            for node in paragraph.iter():
                for attr, rid in node.attrib.items():
                    if not attr.endswith(("}embed", "}dm")) or rid not in targets:
                        continue
                    target = targets[rid]
                    if not (_IMG_RE.match(target) or _DGM_RE.match(target)):
                        continue
                    data = archive.read(target)
                    if len(data) > MAX_IMAGE_BYTES:
                        continue
                    result.append({"data": data, "kind": "image" if _IMG_RE.match(target) else "smartart",
                                   "part": target, "position": position,
                                   "nearby_text": "\n".join(texts[max(0, position-1):position+2])})
        return result


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
    try:
        occurrences = media_occurrences(path)
    except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile):
        occurrences = []
    # Compatibility for older packages with no document relationships.
    if not occurrences:
        occurrences = [{"data": d, "kind": kind, "part": f"{kind}/{i}",
                        "position": None, "nearby_text": ""}
                       for kind, values in (("smartart", diagrams), ("image", images))
                       for i, d in enumerate(values, 1)]
    image_number = 0
    for item in occurrences:
        kind = item["kind"]
        if kind == "image":
            image_number += 1
        label = f"Скриншот {image_number}" if kind == "image" else "Схема"
        source_id = hashlib.sha256((file_name + ":" + item["part"] + ":" +
                                    str(item["position"])).encode() + item["data"]).hexdigest()[:24]
        context = ctx + ("\nБлижайший текст: " + item["nearby_text"] if item["nearby_text"] else "")
        try:
            text = (describe_image if kind == "image" else smartart_to_text)(item["data"], context)
        except Exception as exc:
            print(f"[media] {file_name}: {source_id} failed {exc!r}")
            continue
        if text.strip():
            chunks.append({"text": f"[{label}] " + text,
                           "heading": f"{label}: {ctx}", "section": file_name,
                           "source_id": source_id, "media_part": item["part"],
                           "position": item["position"], "nearby_text": item["nearby_text"]})
    if chunks:
        print(f"[media] {file_name}: +{len(chunks)} медиа-чанков")
    return chunks
