"""№10: xlsx-матрица «сотрудник × курс» — чистый модуль, читаем из bytes.
18.09: статусы текстом + заливка, автофильтр, колонка «Отдел», «Легенда»."""
import json
from io import BytesIO

from openpyxl import load_workbook

import app.report_excel as rx

Q10 = json.dumps({"basic_questions": [], "exam_questions": [
    {"id": i, "text": "?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
     "correct": "A"} for i in range(10)]})     # порог сдачи round(7.0)=7

FIXED = ["Сотрудник", "Должность", "Отдел", "Роль"]
N = len(FIXED)


def _course(cid, name):
    return {"id": cid, "doc_name": name, "questions_json": Q10}


def _row(rid, uid, cid, state, exam=0, name=None, role=None):
    return {"id": rid, "user_id": uid, "course_id": cid, "state": state,
            "score_basic": 3, "score_exam": exam,
            "doc_name": name or f"Курс{cid}.docx", "questions_json": Q10,
            "updated_at": "2026-08-11T10:00:00", "role": role}


def _load(data):
    return load_workbook(BytesIO(data))


def _fill(cell):
    rgb = cell.fill.start_color.rgb if cell.fill and cell.fill.fill_type else ""
    return (rgb or "")[-6:]


def test_matrix_cells_best_of_pair():
    courses = [_course(1, "ALL Первый.docx"), _course(2, "ALL Второй.docx"),
               _course(3, "ALL Третий.docx"), _course(4, "ALL Четвёртый.docx")]
    rows = [
        _row(1, "10", 1, "DONE", exam=8, name="ALL Первый.docx"),   # сдан
        _row(2, "10", 2, "DONE", exam=2, name="ALL Второй.docx"),   # провал…
        _row(3, "10", 2, "DONE", exam=9, name="ALL Второй.docx"),   # …пересдал
        _row(4, "10", 3, "DONE", exam=1, name="ALL Третий.docx"),   # не сдан
        _row(5, "10", 4, "READING", name="ALL Четвёртый.docx"),     # в процессе
    ]
    emps = {"10": {"full_name": "Иван Иванов", "work_position": "Админ"}}
    ws = _load(rx.build_report_xlsx(rows, emps, courses))["Матрица"]
    header = [c.value for c in ws[1]]
    assert header[:N] == FIXED
    assert header[N:] == ["Первый", "Второй", "Третий", "Четвёртый"]
    line = [c.value for c in ws[2]]
    assert line[0] == "Иван Иванов" and line[1] == "Админ"
    # лучший результат пары, текстом вместо эмодзи
    assert line[N:] == ["Сдан 8/10", "Сдан 9/10", "Не сдан 1/10", "Идёт"]
    assert [_fill(ws.cell(2, N + 1 + i)) for i in range(4)] == [
        "C6EFCE", "C6EFCE", "FFC7CE", "FFEB9C"]
    assert _fill(ws.cell(2, 1)) == ""                    # ФИО без заливки


def test_archived_course_and_legacy_uid_present():
    rows = [_row(1, "10", 5, "DONE", exam=8, name="ALL Архивный.docx")]
    ws = _load(rx.build_report_xlsx(rows, {}, []))["Матрица"]  # активных нет
    assert [c.value for c in ws[1]][N] == "Архивный"           # колонка жива
    line = [c.value for c in ws[2]]
    assert line[0] == "ID 10" and line[N] == "Сдан 8/10"       # legacy-строка


def test_not_started_empty_and_details_sheet():
    courses = [_course(1, "ALL Первый.docx")]
    emps = {"20": {"full_name": "Пётр Петров", "work_position": None}}
    wb = _load(rx.build_report_xlsx([], emps, courses))
    assert [c.value for c in wb["Матрица"][2]][N] in ("", None)  # не начинал
    assert "Детали" in wb.sheetnames

    wb2 = _load(rx.build_report_xlsx(
        [_row(1, "20", 1, "DONE", exam=9, name="ALL Первый.docx"),
         _row(2, "20", 1, "BASIC_TEST", name="ALL Первый.docx")],
        emps, courses))
    det = wb2["Детали"]
    header = [c.value for c in det[1]]
    assert header == ["Сотрудник", "Отдел", "Курс", "Статус", "Базовый (старые сессии)",
                      "Тест / экзамен", "Дата"]
    lines = {tuple(c.value for c in det[i]) for i in (2, 3)}
    statuses = {ln[3] for ln in lines}
    assert statuses == {"Сдан 9/10", "проходит базовый тест"}
    assert all(ln[0] == "Пётр Петров" for ln in lines)
    fills = {_fill(det.cell(i, 4)) for i in (2, 3)}
    assert fills == {"C6EFCE", "FFEB9C"}


def test_autofilter_freeze_and_legend():
    courses = [_course(1, "ALL Первый.docx")]
    emps = {"20": {"full_name": "Пётр Петров"}}
    wb = _load(rx.build_report_xlsx(
        [_row(1, "20", 1, "DONE", exam=9, name="ALL Первый.docx")],
        emps, courses))
    ws, det = wb["Матрица"], wb["Детали"]
    assert ws.auto_filter.ref == "A1:E2" and ws.freeze_panes == "B2"
    assert det.auto_filter.ref == "A1:G2" and det.freeze_panes == "A2"
    assert ws["A1"].font.bold
    legend = wb["Легенда"]
    samples = [c.value for c in legend["A"]][1:]
    assert samples[:3] == ["Сдан 7/10", "Не сдан 3/10", "Идёт"]
    assert [_fill(legend.cell(i, 1)) for i in (2, 3, 4)] == [
        "C6EFCE", "FFC7CE", "FFEB9C"]
    assert "не начинал" in legend["B5"].value


def test_department_and_role_columns():
    courses = [_course(1, "ALL Первый.docx")]
    emps = {"20": {"full_name": "Пётр Петров", "role": "admin_reception"},
            "30": {"full_name": "Анна Иванова"}}
    rows = [_row(1, "30", 1, "READING", name="ALL Первый.docx",
                 role="housekeeping")]
    ws = _load(rx.build_report_xlsx(
        rows, emps, courses,
        departments_by_uid={"20": "Ресепшн", "30": "ID 2439"}))["Матрица"]
    by_name = {ws.cell(i, 1).value: [ws.cell(i, j).value for j in range(1, 6)]
               for i in (2, 3)}
    # роль из employees, когда сессий ещё нет; из сессии — когда есть
    assert by_name["Пётр Петров"][2] == "Ресепшн"
    assert by_name["Пётр Петров"][3] != ""
    assert by_name["Анна Иванова"][2] == "ID 2439"
    assert by_name["Анна Иванова"][3] != ""
