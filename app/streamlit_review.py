"""
app/streamlit_review.py

Streamlit-интерфейс для маркировки стандартов по ролям.
Каждый сотрудник выбирает свою роль и раскрашивает стандарты:
  🟢 Зелёный  — работает, нужен
  🔴 Красный  — не нужно делать / устарело
  🟡 Жёлтый  — нужно, но требует переформулировки

Запуск: streamlit run app/streamlit_review.py
"""

import json
import os
import streamlit as st

# ─── КОНФИГ ──────────────────────────────────────────────────────────────────

CHUNKS_PATH   = os.path.join(os.path.dirname(__file__), "..", "data", "chunks_cache.json")
PROGRESS_DIR  = os.path.join(os.path.dirname(__file__), "..", "data", "review_progress")

os.makedirs(PROGRESS_DIR, exist_ok=True)

ROLES = {
    "admin_reception": "🛎️  Администратор ресепшн (СПиР)",
    "supervisor_fo":   "👔  Супервайзер СПиР",
    "housekeeper":     "🧹  Горничная / Уборщик",
    "hsk_supervisor":  "🏠  Руководитель хозяйственной службы",
    "engineer":        "🔧  Инженер / Технический персонал",
    "hr_manager":      "👥  HR менеджер",
    "owner_relations": "💼  Менеджер по работе с собственниками",
    "sales_manager":   "📊  Менеджер по продажам",
    "marketing":       "📣  Менеджер по маркетингу",
    "security":        "🔒  Служба безопасности",
    "general_manager": "🏨  Генеральный менеджер",
    "all_staff":       "👨‍👩‍👧  Все сотрудники",
}

STATUS_OPTIONS = {
    "green":  "🟢 Работает, нужен",
    "yellow": "🟡 Нужен, но переформулировать",
    "red":    "🔴 Не нужно / устарело",
}

STATUS_COLORS = {
    "green":  "#d4edda",
    "yellow": "#fff3cd",
    "red":    "#f8d7da",
    None:     "#f8f9fa",
}


# ─── ЗАГРУЗКА ДАННЫХ ─────────────────────────────────────────────────────────

@st.cache_data
def load_chunks():
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_progress_path(role_id: str) -> str:
    return os.path.join(PROGRESS_DIR, f"{role_id}.json")


def load_progress(role_id: str) -> dict:
    """Загружает сохранённый прогресс {chunk_id: status}"""
    path = get_progress_path(role_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(role_id: str, progress: dict):
    """Сохраняет прогресс {chunk_id: status}"""
    path = get_progress_path(role_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ─── СТАТИСТИКА ──────────────────────────────────────────────────────────────

def show_stats(chunks_for_role: list, progress: dict):
    total = len(chunks_for_role)
    reviewed = sum(1 for c in chunks_for_role if progress.get(str(c["id"])))
    green  = sum(1 for c in chunks_for_role if progress.get(str(c["id"])) == "green")
    yellow = sum(1 for c in chunks_for_role if progress.get(str(c["id"])) == "yellow")
    red    = sum(1 for c in chunks_for_role if progress.get(str(c["id"])) == "red")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Всего", total)
    col2.metric("Проверено", f"{reviewed}/{total}")
    col3.metric("🟢 Зелёных", green)
    col4.metric("🟡 Жёлтых", yellow)
    col5.metric("🔴 Красных", red)

    if total > 0:
        pct = reviewed / total * 100
        st.progress(pct / 100, text=f"Прогресс: {pct:.0f}%")


# ─── ФИЛЬТРЫ ─────────────────────────────────────────────────────────────────

def filter_chunks(chunks_for_role: list, progress: dict,
                  filter_status: str, filter_section: str,
                  search_query: str) -> list:
    result = chunks_for_role

    # Фильтр по статусу
    if filter_status == "Не проверено":
        result = [c for c in result if not progress.get(str(c["id"]))]
    elif filter_status != "Все":
        status_key = {v: k for k, v in STATUS_OPTIONS.items()}.get(filter_status)
        result = [c for c in result if progress.get(str(c["id"])) == status_key]

    # Фильтр по разделу
    if filter_section != "Все разделы":
        result = [c for c in result if c["section"] == filter_section]

    # Поиск по тексту
    if search_query:
        q = search_query.lower()
        result = [c for c in result
                  if q in c["heading"].lower() or q in c["text"].lower()]

    return result


# ─── ГЛАВНАЯ СТРАНИЦА ────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Вертикаль — Проверка стандартов",
        page_icon="🏨",
        layout="wide",
    )

    st.title("🏨 Вертикаль — Проверка стандартов")
    st.caption("Каждый сотрудник проверяет свои стандарты и расставляет оценки")

    # Проверяем наличие данных
    if not os.path.exists(CHUNKS_PATH):
        st.error(f"Файл не найден: `{CHUNKS_PATH}`")
        st.info("Сначала запустите: `python scripts/parse_standards.py --input data/Vertical_franchise_standards.docx`")
        return

    all_chunks = load_chunks()

    # ─── САЙДБАР: выбор роли и фильтры ──────────────────────────────────────
    with st.sidebar:
        st.header("⚙️ Настройки")

        role_id = st.selectbox(
            "Ваша роль",
            options=list(ROLES.keys()),
            format_func=lambda x: ROLES[x],
            index=0,
        )

        st.divider()
        st.subheader("Фильтры")

        # Разделы для этой роли
        chunks_for_role = [c for c in all_chunks if role_id in c["roles"]]
        sections = sorted(set(c["section"] for c in chunks_for_role))

        filter_section = st.selectbox(
            "Раздел",
            options=["Все разделы"] + sections,
        )

        filter_status = st.selectbox(
            "Статус",
            options=["Все", "Не проверено"] + list(STATUS_OPTIONS.values()),
        )

        search_query = st.text_input("🔍 Поиск по тексту")

        st.divider()

        # Экспорт
        progress = load_progress(role_id)
        if st.button("💾 Скачать мой прогресс (JSON)"):
            export = [
                {**c, "status": progress.get(str(c["id"]))}
                for c in chunks_for_role
            ]
            st.download_button(
                label="Скачать",
                data=json.dumps(export, ensure_ascii=False, indent=2),
                file_name=f"review_{role_id}.json",
                mime="application/json",
            )

    # ─── ОСНОВНАЯ ОБЛАСТЬ ────────────────────────────────────────────────────

    role_name = ROLES[role_id]
    chunks_for_role = [c for c in all_chunks if role_id in c["roles"]]
    progress = load_progress(role_id)

    st.subheader(f"{role_name}")

    # Статистика
    show_stats(chunks_for_role, progress)

    st.divider()

    # Применяем фильтры
    filtered = filter_chunks(chunks_for_role, progress,
                             filter_status, filter_section, search_query)

    if not filtered:
        st.info("Нет стандартов по выбранным фильтрам")
        return

    st.write(f"Показано: **{len(filtered)}** стандартов")

    # ─── СПИСОК СТАНДАРТОВ ────────────────────────────────────────────────────

    for chunk in filtered:
        chunk_key = str(chunk["id"])
        current_status = progress.get(chunk_key)
        bg_color = STATUS_COLORS[current_status]

        # Карточка стандарта
        with st.container():
            # Заголовок с цветовым фоном через markdown
            status_label = STATUS_OPTIONS.get(current_status, "⬜ Не проверено")

            st.markdown(
                f"""<div style="
                    background-color: {bg_color};
                    border-radius: 8px;
                    padding: 12px 16px;
                    margin-bottom: 4px;
                    border-left: 4px solid #6c757d;
                ">
                <small style="color: #6c757d; font-size: 11px;">
                    {chunk['code']} | {chunk['section']} | {status_label}
                </small><br/>
                <strong>{chunk['heading']}</strong>
                </div>""",
                unsafe_allow_html=True,
            )

            # Текст (раскрывается)
            with st.expander("📄 Показать текст стандарта"):
                # Разбиваем на абзацы для читаемости
                text = chunk["text"]
                # Простая разбивка по точкам предложений (>50 символов)
                sentences = re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z])', text)
                for s in sentences:
                    if s.strip():
                        st.write(s.strip())

            # Кнопки оценки
            col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
            with col1:
                if st.button("🟢 Работает", key=f"green_{chunk_key}"):
                    progress[chunk_key] = "green"
                    save_progress(role_id, progress)
                    st.rerun()
            with col2:
                if st.button("🟡 Переписать", key=f"yellow_{chunk_key}"):
                    progress[chunk_key] = "yellow"
                    save_progress(role_id, progress)
                    st.rerun()
            with col3:
                if st.button("🔴 Не нужно", key=f"red_{chunk_key}"):
                    progress[chunk_key] = "red"
                    save_progress(role_id, progress)
                    st.rerun()
            with col4:
                if current_status == "yellow":
                    comment = st.text_input(
                        "Комментарий (что изменить):",
                        key=f"comment_{chunk_key}",
                        placeholder="Напишите что нужно переформулировать...",
                    )
                    if comment:
                        # Сохраняем комментарий отдельно
                        comments_path = os.path.join(
                            PROGRESS_DIR, f"{role_id}_comments.json"
                        )
                        comments = {}
                        if os.path.exists(comments_path):
                            with open(comments_path) as f:
                                comments = json.load(f)
                        comments[chunk_key] = comment
                        with open(comments_path, "w", encoding="utf-8") as f:
                            json.dump(comments, f, ensure_ascii=False, indent=2)

            st.markdown("---")


import re

if __name__ == "__main__":
    main()
