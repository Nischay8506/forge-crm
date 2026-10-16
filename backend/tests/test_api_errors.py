"""Whatever goes wrong, the answer keeps the shape the screens read.

A screen shows `error.message` and lists `error.problems` under the fields. When the
API answers in any other shape the person is left with the screen's fallback, which
is how a fresh install with no keys in backend/.env looked: "Could not save the
enquiry", and nothing about the real cause (16 Oct 2026).
"""

from fastapi import Request

from app.config import get_settings
from app.core import keyring
from app.core.errors import unhandled_handler
from tests.test_agents import ctx  # noqa: F401
from tests.test_enquiries import E

ENQUIRY = {"customer": {"company": "Konark Pipes", "phone": "9876543210"},
           "summary": "MS plates for a shed", "received_how": "Phone call",
           "lines": [{"description": "MS plate 10mm", "qty": 5, "unit": "nos"}]}


async def test_a_server_with_no_evidence_key_says_so_instead_of_failing(
        client, ctx, monkeypatch):  # noqa: F811
    """The fresh-install trap: .env.example leaves the key settings empty, so every
    save that writes customer details to the audit trail used to answer a bare 500."""
    assert (await client.post(E, headers=ctx["h"], json=ENQUIRY)).status_code == 201
    monkeypatch.setattr(get_settings(), "evidence_keys", None)
    r = await client.post(E, headers=ctx["h"], json=ENQUIRY)
    assert r.status_code == 503, r.text
    error = r.json()["error"]
    assert error["code"] == "not_configured"
    assert "EVIDENCE_KEYS is not set" in error["message"]
    assert "new-evidence-key" in error["message"]  # and how to make one

    # The same settings are listed at startup, so the log says it before anyone saves.
    assert any("EVIDENCE_KEYS is not set" in p for p in keyring.unusable())
    monkeypatch.setattr(get_settings(), "audit_seal_keys", "oops")
    assert any("AUDIT_SEAL_KEYS entry 1 must look like" in p for p in keyring.unusable())


async def test_a_field_the_api_refuses_is_named_on_the_form(client, ctx):  # noqa: F811
    """FastAPI's own 422 was not in the error shape either, so a value that is too
    long for a column showed as "Could not save the enquiry" with nothing to fix."""
    r = await client.post(E, headers=ctx["h"], json={
        **ENQUIRY, "customer": {"company": "Konark Pipes", "phone": "9" * 40}})
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["problems"] == ["customer phone: String should have at most 30 characters"]

    r = await client.post(E, headers=ctx["h"], json={
        **ENQUIRY, "lines": [{"description": "MS plate", "qty": "ten", "unit": "nos"}]})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["problems"][0].startswith("lines 1 qty: ")


async def test_an_unexpected_error_gives_the_person_a_reference():
    scope = {"type": "http", "method": "POST", "path": "/api/v1/quotes", "headers": [],
             "query_string": b"", "scheme": "http", "server": ("t", 80), "root_path": ""}
    r = await unhandled_handler(Request(scope), RuntimeError("a bug"))
    assert r.status_code == 500
    body = r.body.decode()
    assert "Reference " in body and "a bug" not in body  # the log has the details
