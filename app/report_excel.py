"""
app/report_excel.py — №10: xlsx-отчёт «сотрудник × курс» (матрица Marriott).

Чистый модуль: bytes на выходе, без сети и БД. Правило ячейки зафиксировано:
сдал ХОТЬ ОДНУ сессию пары → «Сдан N/M» (лучший результат); иначе судим
ПОСЛЕДНЮЮ (max id): DONE → «Не сдан N/M», не-DONE → «Идёт»; сессий нет →
пусто. Формула сдачи — как в _finish_phase: score_exam >= round(total * 0.7).

18.09 (фидбек Дмитрия «статусы неоднозначны, нужен фильтр по отделу»):
эмодзи заменены текстом + заливкой (стандартные цвета условного
форматирования Excel), автофильтр на обоих листах, отдельная колонка
«Отдел» (подпись передаёт вызывающий — модуль имён отделов не знает),
лист «Легенда».
"""
import json
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.hr_tools import _STATE_LABELS
from app.roles import display_name, role_name

FILLS = {
    "green": PatternFill("solid", start_color="C6EFCE", end_color="C6EFCE"),
    "red": PatternFill("solid", start_color="FFC7CE", end_color="FFC7CE"),
    "yellow": PatternFill("solid", start_color="FFEB9C", end_color="FFEB9C"),
}

LEGEND = [
    ("Сдан 7/10", "green",
     "экзамен сдан (не меньше 70 % верных), лучший результат из попыток"),
    ("Не сдан 3/10", "red", "последняя попытка экзамена провалена"),
    ("Идёт", "yellow",
     "курс начат: читает, проходит тест, на паузе или ждёт допуска HR"),
    ("", None, "пустая ячейка — сотрудник курс не начинал"),
]

_MATRIX_FIXED = ["Сотрудник", "Должность", "Отдел", "Роль"]
_DETAILS_HEADER = ["Сотрудник", "Отдел", "Курс", "Статус", "Базовый",
                   "Экзамен", "Дата"]


def _questions(obj: dict) -> dict:
    try:
        return json.loads(obj.get("questions_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _exam_total(questions: dict) -> int:
    return len(questions.get("exam_questions", []))


def _passed(session: dict, questions: dict) -> bool:
    total = _exam_total(questions)
    return bool(total) and session["score_exam"] >= round(total * 0.7)


def _cell(pair_sessions: list[dict] | None,
          questions: dict) -> tuple[str, str | None]:
    """(текст, ключ заливки) ячейки матрицы для пары сотрудник × курс."""
    if not pair_sessions:
        return "", None
    total = _exam_total(questions)
    passed = [s for s in pair_sessions
              if s["state"] == "DONE" and _passed(s, questions)]
    if passed:
        best = max(s["score_exam"] for s in passed)   # лучший результат пары
        return f"Сдан {best}/{total}", "green"
    last = max(pair_sessions, key=lambda s: s["id"])
    if last["state"] == "DONE":
        return f"Не сдан {last['score_exam']}/{total}", "red"
    return "Идёт", "yellow"


def _session_cell(session: dict, questions: dict) -> tuple[str, str | None]:
    """Статус одной сессии для листа «Детали» — тот же язык, что в матрице."""
    if session["state"] == "DONE":
        total = _exam_total(questions)
        if _passed(session, questions):
            return f"Сдан {session['score_exam']}/{total}", "green"
        return f"Не сдан {session['score_exam']}/{total}", "red"
    return _STATE_LABELS.get(session["state"], session["state"]), "yellow"


def _write_row(ws, values: list, fills: dict[int, str | None] | None = None):
    """Строка с заливкой отдельных ячеек (fills = {индекс в values: ключ})."""
    row_idx = 1 if ws["A1"].value is None else ws.max_row + 1
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row_idx, column=col, value=value)
        key = (fills or {}).get(col - 1)
        if key:
            cell.fill = FILLS[key]


def _finish_sheet(ws, freeze: str) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = freeze
    ws.auto_filter.ref = ws.dimensions
    for i in range(1, ws.max_column + 1):
        letter = get_column_letter(i)
        width = max((len(str(c.value)) for c in ws[letter] if c.value), default=8)
        ws.column_dimensions[letter].width = min(width + 2, 40)


def build_report_xlsx(rows: list[dict], employees_by_uid: dict[str, dict],
                      courses: list[dict],
                      departments_by_uid: dict[str, str] | None = None) -> bytes:
    """rows = get_report_rows(); courses = get_active_courses();
    departments_by_uid = {uid: «Ресепшн»} — подпись отдела, как её знает бот.

    Колонки = активные курсы ∪ курсы с сессиями (архивный с историей сдач
    не выпадает). Строки = employees ∪ legacy-uid из сессий."""
    departments_by_uid = departments_by_uid or {}
    course_by_id = {c["id"]: c for c in courses}
    for r in rows:
        course_by_id.setdefault(
            r["course_id"],
            {"id": r["course_id"], "doc_name": r["doc_name"],
             "questions_json": r.get("questions_json")})
    course_cols = sorted(course_by_id.values(), key=lambda c: c["id"])

    uids = sorted(employees_by_uid,
                  key=lambda u: employees_by_uid[u].get("full_name") or f"~{u}")
    for r in rows:
        uid = str(r["user_id"])
        if uid not in employees_by_uid and uid not in uids:
            uids.append(uid)                          # legacy-сессии без записи

    by_pair: dict[tuple, list] = {}
    last_role: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        by_pair.setdefault((str(r["user_id"]), r["course_id"]), []).append(r)
        if r.get("role"):
            last_role[str(r["user_id"])] = r["role"]

    def _label(uid: str) -> str:
        emp = employees_by_uid.get(uid, {})
        return emp.get("full_name") or f"ID {uid}"

    wb = Workbook()
    ws = wb.active
    ws.title = "Матрица"
    _write_row(ws, _MATRIX_FIXED
               + [display_name(c["doc_name"]) for c in course_cols])
    fixed = len(_MATRIX_FIXED)
    for uid in uids:
        emp = employees_by_uid.get(uid, {})
        role = last_role.get(uid) or emp.get("role")
        cells = [_cell(by_pair.get((uid, c["id"])), _questions(c))
                 for c in course_cols]
        _write_row(ws,
                   [_label(uid), emp.get("work_position") or "",
                    departments_by_uid.get(uid, ""),
                    role_name(role) if role else ""]
                   + [text for text, _ in cells],
                   {fixed + i: fill for i, (_, fill) in enumerate(cells)})
    _finish_sheet(ws, "B2")

    det = wb.create_sheet("Детали")
    _write_row(det, _DETAILS_HEADER)
    for r in sorted(rows, key=lambda r: r.get("updated_at") or "",
                    reverse=True):
        uid = str(r["user_id"])
        q = _questions(r)
        status, fill = _session_cell(r, q)
        _write_row(det, [
            _label(uid),
            departments_by_uid.get(uid, ""),
            display_name(r["doc_name"]),
            status,
            f"{r['score_basic']}/{len(q.get('basic_questions', []))}",
            f"{r['score_exam']}/{_exam_total(q)}",
            (r.get("updated_at") or "")[:10],
        ], {3: fill})
    _finish_sheet(det, "A2")

    legend = wb.create_sheet("Легенда")
    _write_row(legend, ["Как выглядит", "Что означает"])
    for sample, fill, meaning in LEGEND:
        _write_row(legend, [sample, meaning], {0: fill})
    _finish_sheet(legend, "A2")
    legend.auto_filter.ref = None

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
