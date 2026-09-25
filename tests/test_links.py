from app.links import doc_url

URL = "https://portal.becar.ru/docs/file/%D0%90%20b.docx?x=1"


def test_empty_host_keeps_url():
    assert doc_url(URL, host="") == URL


def test_host_replaced_path_kept():
    assert doc_url(URL, host="propertyhunters.pro") == \
        "https://propertyhunters.pro/docs/file/%D0%90%20b.docx?x=1"


def test_none_and_relative_untouched():
    assert doc_url(None, host="propertyhunters.pro") is None
    assert doc_url("/docs/x", host="propertyhunters.pro") == "/docs/x"
