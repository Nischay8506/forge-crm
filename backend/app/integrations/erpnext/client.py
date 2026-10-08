"""A small client for the Frappe REST API that ERPNext serves (v15 and v16).

It only knows HTTP and Frappe's error shapes; translating records is the adapter's
job. Every failure becomes a ConnectorError with a cause, so the sync engine can
say exactly what went wrong.
"""

import json
import re
from typing import Any

import httpx

from app.integrations.adapter import ConnectorError

REJECTIONS = ("ValidationError", "MandatoryError", "DuplicateEntryError", "LinkValidationError")


def _server_message(body: Any) -> str | None:
    """Frappe puts user-facing errors in _server_messages (a JSON list of JSON dicts)."""
    if not isinstance(body, dict) or not body.get("_server_messages"):
        return None
    try:
        first = json.loads(json.loads(body["_server_messages"])[0])
    except (ValueError, TypeError, IndexError):
        return None
    if not isinstance(first, dict):
        return None
    text = re.sub(r"<[^>]+>", " ", str(first.get("message") or ""))
    return re.sub(r"\s+", " ", text).strip() or None


def _error(r: httpx.Response) -> ConnectorError:
    try:
        body = r.json()
    except ValueError:
        body = None
    exc_type = body.get("exc_type") if isinstance(body, dict) else None
    detail = _server_message(body)
    status = r.status_code
    if status == 401 or exc_type == "AuthenticationError":
        return ConnectorError(
            "auth_rejected", "ERPNext rejected the API key and secret", detail=detail
        )
    if status == 403 or exc_type == "PermissionError":
        return ConnectorError(
            "permission_denied", "The ERPNext user may not do this", detail=detail
        )
    if status == 429:
        return ConnectorError("rate_limited", "ERPNext asked us to slow down", detail=detail)
    if status in (409, 417) or exc_type in REJECTIONS:
        return ConnectorError("rejected", detail or "ERPNext refused the record", detail=detail)
    if status >= 500:
        return ConnectorError("server_error", f"ERPNext failed (HTTP {status})", detail=detail)
    return ConnectorError(
        "bad_response", f"Unexpected answer from ERPNext (HTTP {status})", detail=detail
    )


class FrappeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
        host_header: str | None = None,
    ):
        if not base_url or not api_key or not api_secret:
            raise ConnectorError(
                "not_configured", "The ERPNext address, API key and secret are all needed"
            )
        headers = {"Authorization": f"token {api_key}:{api_secret}", "Accept": "application/json"}
        if host_header:  # a bench serving several sites picks the site by Host
            headers["Host"] = host_header
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, missing_ok: bool = False, **kwargs: Any
    ) -> Any:
        try:
            r = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise ConnectorError("timeout", "ERPNext did not answer in time", detail=str(e)) from e
        except httpx.TransportError as e:
            raise ConnectorError("unreachable", "ERPNext cannot be reached", detail=str(e)) from e
        if r.is_redirect:
            # Frappe redirects to the login page when the session/token is not accepted.
            raise ConnectorError(
                "auth_rejected", "ERPNext sent us to its login page",
                detail=r.headers.get("location"),
            )
        if missing_ok and r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise _error(r)
        try:
            return r.json()
        except ValueError as e:
            raise ConnectorError(
                "bad_response", "ERPNext answered with something that is not JSON",
                detail=r.text[:200],
            ) from e

    async def method(self, name: str, **params: Any) -> Any:
        body = await self._request("GET", f"/api/method/{name}", params=params)
        return body.get("message") if isinstance(body, dict) else body

    async def get_list(
        self,
        doctype: str,
        *,
        fields: list[str],
        filters: list[list[Any]] | None = None,
        order_by: str | None = None,
        start: int = 0,
        limit: int = 100,
        parent: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fields": json.dumps(fields),
            "limit_start": start,
            "limit_page_length": limit,
        }
        if filters:
            params["filters"] = json.dumps(filters, default=str)
        if order_by:
            params["order_by"] = order_by
        if parent:
            params["parent"] = parent
        body = await self._request("GET", f"/api/resource/{doctype}", params=params)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise ConnectorError("bad_response", f"ERPNext's {doctype} list had no data")
        return data

    async def get_doc(self, doctype: str, name: str) -> dict[str, Any] | None:
        body = await self._request("GET", f"/api/resource/{doctype}/{name}", missing_ok=True)
        if body is None:
            return None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ConnectorError("bad_response", f"ERPNext's {doctype} {name} had no data")
        return data

    async def insert(self, doctype: str, doc: dict[str, Any]) -> dict[str, Any]:
        body = await self._request("POST", f"/api/resource/{doctype}", json=doc)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ConnectorError("bad_response", f"ERPNext did not return the new {doctype}")
        return data
