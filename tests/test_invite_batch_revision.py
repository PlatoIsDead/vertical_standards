import asyncio

import pytest

from app import bitrix_bot as bot


def test_resolver_continues_after_failure(monkeypatch):
    async def lookup(uid):
        if uid == "1":
            raise RuntimeError("API")
        return {"ID": uid}
    monkeypatch.setattr(bot, "_bitrix_user_by_id", lookup)
    result = asyncio.run(bot._resolve_invite_tokens(["1", "2", "2"]))
    assert result["targets"] == [{"ID": "2"}]
    assert result["errors"][0][0] == "1"


def test_multiple_candidate_choices_preserve_targets(monkeypatch):
    bot._pending_edits.clear()
    found = {"targets": [{"ID": "1"}], "ambiguous": [("a", [{"ID": "2"}, {"ID": "3"}]),
             ("b", [{"ID": "4"}, {"ID": "5"}])], "missing": [], "no_scope": False, "dept_preview": None}
    async def run():
        await bot._invite_resolved("hr", found)
        assert bot._pending_edits["hr"]["kind"] == "invite_candidates"
        await bot._candidate_input("hr", bot._pending_edits["hr"], "2")
        assert [u["ID"] for u in found["targets"]] == ["1", "3"]
        assert found["ambiguous"][0][0] == "b"
        # Stop before role selection; verify skip is retained.
        async def resolved(hr, result):
            assert result["notes"] == ["«b» — пропущен."]
            return "done", None
        monkeypatch.setattr(bot, "_invite_resolved", resolved)
        await bot._candidate_input("hr", bot._pending_edits["hr"], "0")
    asyncio.run(run())


def test_batch_error_does_not_stop_remaining(monkeypatch):
    calls = []
    async def finish(uid, *args):
        calls.append(uid)
        if uid == "1":
            raise RuntimeError("failed")
        return "invited 2"
    monkeypatch.setattr(bot, "_finish_invite", finish)
    pending = {"targets": [{"uid": uid, "email": "", "fio": uid, "work_position": None} for uid in ("1", "2")]}
    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(bot._finish_invite_batch(pending, None, "hr"))
    assert calls == ["1", "2"]
    assert [t["uid"] for t in pending["targets"]] == ["1"]
    async def recovered(uid, *args):
        calls.append(uid)
        return "invited 1"
    monkeypatch.setattr(bot, "_finish_invite", recovered)
    result = asyncio.run(bot._finish_invite_batch(pending, None, "hr"))
    assert calls == ["1", "2", "1"]
    assert "invited 2" in result and "invited 1" in result
    assert pending["targets"] == []
