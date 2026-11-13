import zipfile

from app import media_ingest as media


def test_versioned_context_cache_keys():
    assert media.vision_cache_key(b"same", "one", "image") != media.vision_cache_key(b"same", "two", "image")
    assert media.vision_cache_key(b"same", "one", "image").startswith(media.VISION_CACHE_VERSION)


def test_relationship_order_context_and_stable_ids(tmp_path, monkeypatch):
    path = tmp_path / "sample.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:t>First caption</w:t><a:blip r:embed="r2"/></w:r></w:p><w:p><w:r><w:t>Second caption</w:t><a:blip r:embed="r1"/></w:r></w:p></w:body></w:document>''')
        z.writestr("word/_rels/document.xml.rels", '<Relationships><Relationship Id="r1" Target="media/image1.png"/><Relationship Id="r2" Target="media/image2.png"/></Relationships>')
        z.writestr("word/media/image1.png", b"one")
        z.writestr("word/media/image2.png", b"two")
    monkeypatch.setattr(media, "describe_image", lambda data, context: data.decode())
    chunks = media.docx_media_chunks(str(path), "sample")
    assert [c["text"] for c in chunks] == ["[Скриншот 1] two", "[Скриншот 2] one"]
    assert chunks[0]["source_id"] != chunks[1]["source_id"]
    assert "First caption" in chunks[0]["nearby_text"]
    assert chunks == media.docx_media_chunks(str(path), "sample")
