"""A fake ERPNext for tests: the parts of the Frappe REST API the adapter uses, in memory.

It answers like ERPNext v16 does (checked against a real v16 site): token login,
401 AuthenticationError for a wrong or revoked key, 403 PermissionError for a record
type the user may not read, child tables readable only with `parent=<doctype>`,
filters / order_by / limit_start / limit_page_length on /api/resource, and times in
the site's time zone without an offset.

Faults can be switched on to test failure paths: offline, timeout, server_error,
rate_limited, not_json.
"""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

CHILD_OF = {
    "Customer Credit Limit": ("Customer", "credit_limits"),
    "Sales Order Item": ("Sales Order", "items"),
    "Sales Invoice Item": ("Sales Invoice", "items"),
    "Payment Entry Reference": ("Payment Entry", "references"),
}
FAULTS = ("offline", "timeout", "server_error", "rate_limited", "not_json")


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


def _frappe_error(status: int, exc_type: str, message: str) -> httpx.Response:
    msg = json.dumps({"message": message, "title": "Message", "indicator": "red"})
    return _json(status, {"exc_type": exc_type, "_server_messages": json.dumps([msg])})


def _cmp_value(v: Any) -> Any:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else v


def _matches(row: dict[str, Any], flt: list[Any]) -> bool:
    field, op, value = flt[-3], flt[-2], flt[-1]
    have = row.get(field)
    if op == "=":
        return _cmp_value(have) == _cmp_value(value) or (have is None and value in ("", None))
    if op == "!=":
        return _cmp_value(have) != _cmp_value(value)
    if op == "like":
        needle = str(value).strip("%").lower()
        return have is not None and needle in str(have).lower()
    if op == "in":
        return have in value
    if op == "not in":
        return have not in value
    if have is None:
        return False
    a, b = _cmp_value(have), _cmp_value(value)
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


class FakeERPNext(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        api_key: str = "fake-key",
        api_secret: str = "fake-secret",
        user: str = "forge.sync@example.com",
        time_zone: str = "Asia/Kolkata",
        version: str = "16.50.0",
    ):
        self.api_key, self.api_secret, self.user = api_key, api_secret, user
        self.tz = ZoneInfo(time_zone)
        self.version = version
        self.docs: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.child_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.denied: set[str] = set()
        self.read_only: set[str] = set()  # doctypes the key may read but not create
        self.shares_time_zone = False  # whether the key may read System Settings
        self.fault: str | None = None
        # Do the next insert, then answer as if the connection dropped (lost answer).
        self.lose_next_answer = False
        self.revoked = False
        self.requests: list[httpx.Request] = []
        self._clock = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)

    # --- seeding -----------------------------------------------------------------------

    def tick(self, seconds: float = 1.0) -> str:
        """Advance the fake clock; returns the new time as ERPNext writes it (local, no offset)."""
        self._clock += timedelta(seconds=seconds)
        return self.local(self._clock)

    def catch_up(self) -> None:
        """Move the fake clock to now, for changes compared with Forge's own clock."""
        self._clock = max(self._clock, datetime.now(UTC))

    def local(self, at: datetime) -> str:
        local = at.astimezone(self.tz).replace(tzinfo=None)
        return local.isoformat(sep=" ", timespec="microseconds")

    def add(
        self, doctype: str, row: dict[str, Any], **children: list[dict[str, Any]]
    ) -> dict[str, Any]:
        row = {"docstatus": 0, "disabled": 0, **row}
        row.setdefault("modified", self.tick())
        self.docs[doctype][row["name"]] = row
        for child_doctype, (parent_doctype, key) in CHILD_OF.items():
            if parent_doctype == doctype and key in children:
                for idx, c in enumerate(children[key], start=1):
                    self.child_rows[child_doctype].append(
                        {"idx": idx, **c, "parent": row["name"], "parenttype": doctype,
                         "parentfield": key, "name": f"{row['name']}-{key}-{idx}"}
                    )
        return row

    def touch(self, doctype: str, name: str, **changes: Any) -> None:
        self.docs[doctype][name].update(changes, modified=self.tick())

    def revoke(self) -> None:
        self.revoked = True

    # --- HTTP --------------------------------------------------------------------------

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fault == "offline":
            raise httpx.ConnectError("connection refused", request=request)
        if self.fault == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if self.fault == "server_error":
            return _json(500, {"exc_type": "OperationalError", "exception": "Deadlock found"})
        if self.fault == "rate_limited":
            return _frappe_error(429, "RateLimitExceededError", "Too many requests")
        if self.fault == "not_json":
            return httpx.Response(200, text="<html>Maintenance</html>")

        auth = request.headers.get("authorization", "")
        if auth != f"token {self.api_key}:{self.api_secret}" or self.revoked:
            return _json(401, {"exc_type": "AuthenticationError"})

        path = unquote(request.url.path)
        params = request.url.params
        if path.startswith("/api/method/"):
            return self._method(path.removeprefix("/api/method/"))
        if path.startswith("/api/resource/"):
            rest = path.removeprefix("/api/resource/")
            doctype, _, name = rest.partition("/")
            if request.method == "GET" and name:
                return self._get_doc(doctype, name)
            if request.method == "GET":
                return self._get_list(doctype, params)
            if request.method == "POST" and not name:
                response = self._insert(doctype, json.loads(request.content or b"{}"))
                if self.lose_next_answer and response.status_code == 200:
                    self.lose_next_answer = False
                    raise httpx.ReadTimeout("timed out", request=request)
                return response
        return _frappe_error(404, "PageDoesNotExistError", "Page not found")

    def _method(self, name: str) -> httpx.Response:
        if name == "frappe.auth.get_logged_user":
            return _json(200, {"message": self.user})
        if name == "frappe.utils.change_log.get_versions":
            return _json(200, {"message": {
                "frappe": {"title": "Frappe Framework", "version": self.version},
                "erpnext": {"title": "ERPNext", "version": self.version},
            }})
        if name == "frappe.client.get_single_value":
            if self.shares_time_zone:
                return _json(200, {"message": self.tz.key})
            return _frappe_error(403, "PermissionError", "No permission for System Settings")
        return _frappe_error(404, "DoesNotExistError", f"Method {name} not found")

    def _readable(self, doctype: str) -> httpx.Response | None:
        if doctype in self.denied:
            return _frappe_error(403, "PermissionError", f"Insufficient Permission for {doctype}")
        return None

    def _get_doc(self, doctype: str, name: str) -> httpx.Response:
        if (denied := self._readable(doctype)) is not None:
            return denied
        row = self.docs.get(doctype, {}).get(name)
        if row is None:
            return _frappe_error(404, "DoesNotExistError", f"{doctype} {name} not found")
        doc = dict(row)
        for child_doctype, (parent_doctype, key) in CHILD_OF.items():
            if parent_doctype == doctype:
                doc[key] = [c for c in self.child_rows[child_doctype] if c["parent"] == name]
        return _json(200, {"data": doc})

    def _insert(self, doctype: str, doc: dict[str, Any]) -> httpx.Response:
        if doctype in self.denied or doctype in self.read_only:
            return _frappe_error(403, "PermissionError", f"No permission to create {doctype}")
        if doctype == "Customer":
            if not doc.get("customer_name"):
                return _frappe_error(417, "MandatoryError",
                                     "Error: Value missing for Customer: Customer Name")
            # Like ERPNext: the name is the customer name, made unique with " - 1", " - 2".
            base, n = doc["customer_name"], 0
            name = base
            while name in self.docs["Customer"]:
                n += 1
                name = f"{base} - {n}"
            doc = {"customer_type": "Company", **doc, "name": name}
        elif "name" not in doc:
            return _frappe_error(417, "MandatoryError", "name missing")
        row = self.add(doctype, doc)
        return _json(200, {"data": dict(row)})

    def _get_list(self, doctype: str, params: httpx.QueryParams) -> httpx.Response:
        if (denied := self._readable(doctype)) is not None:
            return denied
        if doctype in CHILD_OF:
            if params.get("parent") != CHILD_OF[doctype][0]:
                return _frappe_error(
                    403, "PermissionError", f"Insufficient Permission for {doctype}"
                )
            rows = list(self.child_rows[doctype])
        else:
            rows = list(self.docs.get(doctype, {}).values())
        for flt in json.loads(params.get("filters") or "[]"):
            rows = [r for r in rows if _matches(r, flt)]
        order_by = [p.strip() for p in (params.get("order_by") or "").split(",") if p.strip()]
        for part in reversed(order_by):
            field, _, direction = part.partition(" ")
            rows.sort(key=lambda r, f=field: (r.get(f) is None, _cmp_value(r.get(f))),
                      reverse=direction.strip().lower() == "desc")
        start = int(params.get("limit_start") or 0)
        length = int(params.get("limit_page_length") or 20)
        rows = rows[start:start + length] if length else rows[start:]
        fields = json.loads(params.get("fields") or '["name"]')
        if fields != ["*"]:
            rows = [{f: r.get(f) for f in fields} for r in rows]
        return _json(200, {"data": rows})
