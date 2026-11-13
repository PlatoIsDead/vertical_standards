"""Resolve provenance conservatively; approval always comes from current DB state."""
from app.db import _conn


def reconcile_sources(chunks: list[dict]) -> list[dict]:
    with _conn() as conn:
        courses = [dict(r) for r in conn.execute("SELECT * FROM courses")]
        processed = [dict(r) for r in conn.execute("SELECT * FROM processed_files")]
    report = []
    for index, chunk in enumerate(chunks):
        explicit = chunk.get("course_id")
        file_id = chunk.get("file_id") or chunk.get("doc_id")
        if explicit is not None:
            matches = [c for c in courses if str(c["id"]) == str(explicit)]
            method = "course_id"
        else:
            matches = [c for c in courses if file_id and str(c["doc_id"]) == str(file_id)]
            method = "file_id"
            if not matches:
                key = f"{chunk.get('folder_id')}/{chunk.get('doc_name')}"
                ids = {str(p["file_id"]) for p in processed
                       if p["doc_name"] == chunk.get("doc_name")
                       and p["folder_id"] is not None and chunk.get("folder_id") is not None
                       and str(p["folder_id"]) == str(chunk["folder_id"])}
                matches = [c for c in courses if c.get("source_key") == key
                           or (c["doc_name"] == chunk.get("doc_name") and str(c["doc_id"]) in ids)]
                method = "name_folder"
        course = matches[0] if len(matches) == 1 else None
        report.append({"chunk_index": index, "doc_name": chunk.get("doc_name"),
                       "method": method, "course_id": course["id"] if course else None,
                       "status": "matched" if course else "ambiguous" if matches else "unknown",
                       "available": bool(course and course["approved_at"] and not course["archived_at"])})
    return report


def available_chunks(chunks: list[dict]) -> list[dict]:
    return [c for c, r in zip(chunks, reconcile_sources(chunks)) if r["available"]]
