"""A HubSpot CRM adapter over the public v3/v4 REST API (companies, contacts, email and note engagements, deals,
associations), plus an in-process mock with the same request and response shapes for the demo and the tests.

The adapter is tested against fixture responses shaped like HubSpot's documented ones and against the mock; it has
not been run against a real HubSpot portal for this repository. Association type ids are HubSpot's defaults
(check them in your portal if you use custom associations).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

ASSOC = {
    ("contacts", "companies"): 279,
    ("emails", "contacts"): 198,
    ("emails", "companies"): 186,
    ("notes", "contacts"): 202,
    ("notes", "companies"): 190,
    ("deals", "companies"): 341,
    ("deals", "contacts"): 3,
}


class CRMError(RuntimeError):
    pass


def _assoc(from_type: str, to_type: str, to_id: str) -> dict[str, Any]:
    return {"to": {"id": to_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": ASSOC[(from_type, to_type)]}]}


class HubSpotClient:
    def __init__(self, *, base_url: str, token: str | None, transport: httpx.BaseTransport | None = None, timeout: float = 20.0) -> None:
        if not token:
            raise CRMError("HUBSPOT_TOKEN is not set (a private app token with CRM object write scopes)")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            transport=transport,
            timeout=timeout,
        )
        self.requests = 0

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(3):
            self.requests += 1
            response = self._client.request(method, path, json=body)
            if response.status_code == 429 and attempt < 2:
                time.sleep(min(float(response.headers.get("Retry-After", "1")), 10.0))
                continue
            if response.status_code >= 400:
                raise CRMError(f"HubSpot {method} {path} -> {response.status_code}: {response.text[:200]}")
            return response.json() if response.content else {}
        raise CRMError("HubSpot rate limit")

    def _search(self, object_type: str, prop: str, value: str) -> str | None:
        body = {"filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}], "limit": 1, "properties": [prop]}
        data = self._request("POST", f"/crm/v3/objects/{object_type}/search", body)
        results = data.get("results") or []
        return str(results[0]["id"]) if results else None

    def upsert_company(self, domain: str, properties: dict[str, Any]) -> str:
        props = {"domain": domain, **properties}
        existing = self._search("companies", "domain", domain)
        if existing:
            self._request("PATCH", f"/crm/v3/objects/companies/{existing}", {"properties": props})
            return existing
        return str(self._request("POST", "/crm/v3/objects/companies", {"properties": props})["id"])

    def upsert_contact(self, email: str, properties: dict[str, Any], company_id: str | None = None) -> str:
        props = {"email": email, **properties}
        existing = self._search("contacts", "email", email)
        if existing:
            self._request("PATCH", f"/crm/v3/objects/contacts/{existing}", {"properties": props})
            contact_id = existing
        else:
            contact_id = str(self._request("POST", "/crm/v3/objects/contacts", {"properties": props})["id"])
        if company_id:
            self._request("PUT", f"/crm/v4/objects/contacts/{contact_id}/associations/default/companies/{company_id}")
        return contact_id

    def log_email(self, *, contact_id: str, company_id: str | None, subject: str, text: str, direction: str, when: dt.datetime) -> str:
        associations = [_assoc("emails", "contacts", contact_id)]
        if company_id:
            associations.append(_assoc("emails", "companies", company_id))
        props = {
            "hs_timestamp": when.astimezone(dt.UTC).isoformat().replace("+00:00", "Z"),
            "hs_email_direction": direction,  # EMAIL (sent) or INCOMING_EMAIL
            "hs_email_status": "SENT",
            "hs_email_subject": subject,
            "hs_email_text": text,
        }
        return str(self._request("POST", "/crm/v3/objects/emails", {"properties": props, "associations": associations})["id"])

    def create_note(self, *, body: str, contact_id: str | None, company_id: str | None, when: dt.datetime) -> str:
        associations = []
        if contact_id:
            associations.append(_assoc("notes", "contacts", contact_id))
        if company_id:
            associations.append(_assoc("notes", "companies", company_id))
        props = {"hs_note_body": body, "hs_timestamp": when.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")}
        return str(self._request("POST", "/crm/v3/objects/notes", {"properties": props, "associations": associations})["id"])

    def create_deal(self, *, name: str, stage: str, company_id: str | None, contact_id: str | None) -> str:
        associations = []
        if company_id:
            associations.append(_assoc("deals", "companies", company_id))
        if contact_id:
            associations.append(_assoc("deals", "contacts", contact_id))
        props = {"dealname": name, "pipeline": "default", "dealstage": stage}
        return str(self._request("POST", "/crm/v3/objects/deals", {"properties": props, "associations": associations})["id"])


class MockHubSpot:
    """An in-process stand-in for the HubSpot API used in mock mode: same paths, payloads and response shapes,
    stored through the callbacks (the Scout database in the app, a dict in tests)."""

    def __init__(
        self,
        *,
        insert: Callable[[str, dict[str, Any], list[dict[str, Any]]], str],
        update: Callable[[str, str, dict[str, Any]], bool],
        find: Callable[[str, str, str], str | None],
        associate: Callable[[str, str, str, str], None],
    ) -> None:
        self._insert, self._update, self._find, self._associate = insert, update, find, associate
        self.log: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.log.append((request.method, request.url.path))
        if not request.headers.get("authorization", "").startswith("Bearer ") or len(request.headers["authorization"]) < 12:
            return httpx.Response(401, json={"status": "error", "category": "INVALID_AUTHENTICATION", "message": "missing token"})
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        now = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/search", path):
            f = body["filterGroups"][0]["filters"][0]
            found = self._find(m.group(1), f["propertyName"], f["value"])
            results = [{"id": found, "properties": {f["propertyName"]: f["value"]}, "createdAt": now, "updatedAt": now, "archived": False}] if found else []
            return httpx.Response(200, json={"total": len(results), "results": results})
        if (m := re.fullmatch(r"/crm/v3/objects/(\w+)", path)) and request.method == "POST":
            if "properties" not in body:
                return httpx.Response(400, json={"status": "error", "category": "VALIDATION_ERROR", "message": "properties required"})
            new_id = self._insert(m.group(1), body["properties"], body.get("associations") or [])
            return httpx.Response(201, json={"id": new_id, "properties": body["properties"], "createdAt": now, "updatedAt": now, "archived": False})
        if (m := re.fullmatch(r"/crm/v3/objects/(\w+)/(\w+)", path)) and request.method == "PATCH":
            if not self._update(m.group(1), m.group(2), body.get("properties") or {}):
                return httpx.Response(404, json={"status": "error", "category": "OBJECT_NOT_FOUND", "message": "not found"})
            return httpx.Response(200, json={"id": m.group(2), "properties": body.get("properties") or {}, "updatedAt": now, "archived": False})
        if (m := re.fullmatch(r"/crm/v4/objects/(\w+)/(\w+)/associations/default/(\w+)/(\w+)", path)) and request.method == "PUT":
            self._associate(m.group(1), m.group(2), m.group(3), m.group(4))
            return httpx.Response(200, json={"status": "COMPLETE", "results": [{"from": {"id": m.group(2)}, "to": {"id": m.group(4)}}]})
        return httpx.Response(404, json={"status": "error", "message": f"no mock route for {request.method} {path}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def memory_mock() -> tuple[MockHubSpot, dict[str, dict[str, Any]]]:
    """A mock backed by a dict (tests)."""
    objects: dict[str, dict[str, Any]] = {}

    def insert(object_type: str, props: dict[str, Any], associations: list[dict[str, Any]]) -> str:
        new_id = str(1000 + len(objects))
        objects[new_id] = {"type": object_type, "properties": dict(props), "associations": list(associations)}
        return new_id

    def update(object_type: str, object_id: str, props: dict[str, Any]) -> bool:
        if object_id not in objects:
            return False
        objects[object_id]["properties"].update(props)
        return True

    def find(object_type: str, prop: str, value: str) -> str | None:
        return next((k for k, v in objects.items() if v["type"] == object_type and str(v["properties"].get(prop, "")).lower() == value.lower()), None)

    def associate(from_type: str, from_id: str, to_type: str, to_id: str) -> None:
        objects[from_id]["associations"].append({"to": {"id": to_id}, "type": to_type})

    return MockHubSpot(insert=insert, update=update, find=find, associate=associate), objects
