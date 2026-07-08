import os
import sys
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.rag import load_index, answer

st.set_page_config(page_title="Вертикаль", page_icon="🏨", layout="centered")

st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
.block-container { max-width: 700px; padding-top: 2rem; }
.ans {
    border-left: 4px solid #2E75B6;
    padding: 1rem 1.2rem;
    margin-top: 1rem;
    line-height: 1.75;
    font-size: 1rem;
}
.chip {
    display: inline-block;
    background: #EEF4FB;
    border: 1px solid #B5D4F4;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 0.72rem;
    color: #185FA5;
    margin: 2px;
}
</style>
""", unsafe_allow_html=True)

@st.cache_resource(show_spinner="Загружаю базу знаний...")
def get_index():
    return load_index()

chunks, embeddings = get_index()

st.markdown("# 🏨 Вертикаль — База знаний")
st.caption("Ответы на вопросы по стандартам сети")
st.divider()

SECTIONS = [
    "Общее",
    "Чрезвычайные ситуации",
    "Управление персоналом",
    "Служба приёма и размещения",
    "Хозяйственная служба",
    "Работа с собственниками",
    "Отдел продаж",
    "Маркетинг и продвижение",
    "Административная политика",
]

c1, c2 = st.columns([2, 1])
with c1:
    section = st.selectbox("Раздел", SECTIONS, label_visibility="collapsed")
with c2:
    length = st.select_slider("Длина", ["Коротко", "Стандартно", "Подробно"], value="Стандартно", label_visibility="collapsed")

query = st.text_input("Вопрос", placeholder="Что делать при пожаре? / Стандарт внешнего вида?", label_visibility="collapsed")
go = st.button("Найти ответ", type="primary", use_container_width=True)

if go and query.strip():
    with st.spinner("Ищу..."):
        text, used = answer(
            query=query.strip(),
            chunks=chunks,
            embeddings=embeddings,
            section_filter=None if section == "Общее" else section,
            answer_length=length,
        )
    st.markdown(f'<div class="ans">{text}</div>', unsafe_allow_html=True)
    if used:
        st.markdown("<br>", unsafe_allow_html=True)
        seen, html = set(), ""
        for c in used[:4]:
            lbl = c["heading"][:60]
            if lbl not in seen:
                seen.add(lbl)
                html += f'<span class="chip">📄 {lbl}</span> '
        st.markdown(html, unsafe_allow_html=True)
elif go:
    st.warning("Введите вопрос")

st.divider()
st.caption("Vertical Hospitality · Стандарты 2025")
