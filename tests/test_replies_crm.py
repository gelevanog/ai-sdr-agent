"""Reply rules and the classifier with the offline model; the HubSpot adapter against fixture responses and the mock."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from scout.config import Settings
from scout.crm.hubspot import ASSOC, CRMError, HubSpotClient, memory_mock
from scout.llm.factory import build_chat_model
from scout.replies.classify import classify_reply, load_replies, rule_label
from tests.conftest import FIXTURES, ROOT, TODAY
from tests.test_research_qualify import Scripted

HUBSPOT = FIXTURES / "hubspot"
TOKEN = "pat-" + "test-" + "1" * 6  # built at runtime


@pytest.mark.parametrize(
    "body",
    ["Unsubscribe.", "Please remove me from your list", "Take me off your list.", "STOP", "Do not contact me again.",
     "Not interested, and please don't email me again.", "Under GDPR I'm asking you to erase my personal data.", "No more emails please."],
)  # fmt: skip
def test_opt_out_rules(body: str) -> None:
    assert rule_label("Re: hi", body, "p@x.example") == "unsubscribe"


def test_bounce_and_out_of_office_rules() -> None:
    assert (
        rule_label("Delivery Status Notification (Failure)", "550 5.1.1 user unknown", "mailer-daemon@x.example")
        == "bounce"
    )
    assert rule_label("Automatic reply", "I am out of the office until 14 October.", "p@x.example") == "out_of_office"
    assert rule_label("Re: hi", "Sounds good, let's talk Thursday.", "p@x.example") is None
    assert rule_label("Re: hi", "We stopped using paper logs years ago.", "p@x.example") is None


def test_every_hand_written_opt_out_is_caught_by_the_rules() -> None:
    cases = load_replies(ROOT / "data" / "replies.yaml")
    assert len(cases) == 100
    opt_outs = [c for c in cases if c.label == "unsubscribe"]
    assert all(rule_label(c.subject, c.body, "p@x.example") == "unsubscribe" for c in opt_outs)
    non_opt_outs = [c for c in cases if c.label != "unsubscribe"]
    assert not any(rule_label(c.subject, c.body, "p@x.example") == "unsubscribe" for c in non_opt_outs)


def test_classifier_validates_the_model_reply() -> None:
    model = Scripted(
        {"label": "referral", "referral_email": "someone@else.example", "resume_on": "2031-01-01", "confidence": 2}
    )
    result, calls = classify_reply(
        "Re: x", "Talk to Jo in ops.", "p@x.example", model=model, seller="Wayline", today=TODAY
    )
    assert calls == 1 and result.label == "referral"
    assert result.referral_email is None  # not in the reply: never acted on
    assert result.resume_on is None and result.confidence == 1.0
    assert "<<REPLY>>" in model.prompts[0][1]["content"]


def test_offline_classifier_end_to_end() -> None:
    fake = build_chat_model(Settings(llm_provider="fake"))
    result, calls = classify_reply(
        "Re: x", "We already use TrackRight across the fleet.", "p@x.example", model=fake, seller="Wayline", today=TODAY
    )
    assert (result.label, result.objection_type, calls) == ("objection", "competitor", 1)
    rules, calls = classify_reply("Re: x", "remove me", "p@x.example", model=fake, seller="Wayline", today=TODAY)
    assert (rules.label, rules.source, calls) == ("unsubscribe", "rules", 0)


# ------------------------------------------------------------------ HubSpot
def fixture_transport(routes: dict[tuple[str, str], str], log: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        for (method, prefix), name in routes.items():
            if request.method == method and request.url.path.startswith(prefix):
                status = 201 if method == "POST" and not prefix.endswith("search") else 200
                return httpx.Response(status, json=json.loads((HUBSPOT / name).read_text()))
        return httpx.Response(404, json={"status": "error"})

    return httpx.MockTransport(handler)


def test_hubspot_request_shapes_against_fixture_responses() -> None:
    log: list[httpx.Request] = []
    routes = {
        ("POST", "/crm/v3/objects/companies/search"): "search_empty.json",
        ("POST", "/crm/v3/objects/contacts/search"): "search_empty.json",
        ("POST", "/crm/v3/objects/companies"): "company_created.json",
        ("POST", "/crm/v3/objects/contacts"): "contact_created.json",
        ("PUT", "/crm/v4/objects/contacts"): "association_created.json",
        ("POST", "/crm/v3/objects/emails"): "email_created.json",
        ("POST", "/crm/v3/objects/deals"): "deal_created.json",
    }
    client = HubSpotClient(base_url="https://api.hubapi.test", token=TOKEN, transport=fixture_transport(routes, log))
    company = client.upsert_company("brightwater-fs.example", {"name": "Brightwater Field Services"})
    contact = client.upsert_contact("ruth.delgado@brightwater-fs.example", {"firstname": "Ruth"}, company)
    email = client.log_email(
        contact_id=contact,
        company_id=company,
        subject="Hi",
        text="Body",
        direction="EMAIL",
        when=dt.datetime(2026, 10, 1, 15, 5, tzinfo=dt.UTC),
    )
    deal = client.create_deal(
        name="Brightwater - Wayline Fleet", stage="appointmentscheduled", company_id=company, contact_id=contact
    )
    assert (company, contact, email, deal) == ("18151398412", "95102", "52771301", "30112877")
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in log)
    search = json.loads(log[0].content)
    assert search["filterGroups"][0]["filters"][0] == {
        "propertyName": "domain",
        "operator": "EQ",
        "value": "brightwater-fs.example",
    }
    assert log[4].url.path == "/crm/v4/objects/contacts/95102/associations/default/companies/18151398412"
    email_body = json.loads(log[5].content)
    assert email_body["properties"]["hs_timestamp"] == "2026-10-01T15:05:00Z"
    assert {a["types"][0]["associationTypeId"] for a in email_body["associations"]} == {
        ASSOC[("emails", "contacts")],
        ASSOC[("emails", "companies")],
    }
    deal_body = json.loads(log[6].content)
    assert deal_body["properties"] == {
        "dealname": "Brightwater - Wayline Fleet",
        "pipeline": "default",
        "dealstage": "appointmentscheduled",
    }


def test_hubspot_upsert_updates_an_existing_company() -> None:
    log: list[httpx.Request] = []
    routes = {
        ("POST", "/crm/v3/objects/companies/search"): "search_company_found.json",
        ("PATCH", "/crm/v3/objects/companies/"): "company_created.json",
    }
    client = HubSpotClient(base_url="https://api.hubapi.test", token=TOKEN, transport=fixture_transport(routes, log))
    assert client.upsert_company("brightwater-fs.example", {"name": "B"}) == "18151398412"
    assert [r.method for r in log] == ["POST", "PATCH"]


def test_hubspot_errors_and_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(401, json=json.loads((HUBSPOT / "error_unauthorized.json").read_text()))

    client = HubSpotClient(base_url="https://api.hubapi.test", token=TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(CRMError, match="401"):
        client.upsert_company("x.example", {})
    assert calls["n"] == 2
    with pytest.raises(CRMError, match="HUBSPOT_TOKEN"):
        HubSpotClient(base_url="https://x.test", token=None)


def test_mock_hubspot_round_trip() -> None:
    mock, objects = memory_mock()
    client = HubSpotClient(base_url="https://mock.test", token=TOKEN, transport=mock.transport())
    company = client.upsert_company("a.example", {"name": "A"})
    assert client.upsert_company("a.example", {"name": "A2"}) == company
    contact = client.upsert_contact("x@a.example", {"firstname": "X"}, company)
    deal = client.create_deal(name="A - deal", stage="qualifiedtobuy", company_id=company, contact_id=contact)
    assert objects[company]["properties"]["name"] == "A2"
    assert objects[deal]["type"] == "deals" and len(objects[deal]["associations"]) == 2
    unauthorized = HubSpotClient(base_url="https://mock.test", token="x", transport=mock.transport())
    with pytest.raises(CRMError, match="401"):
        unauthorized.upsert_company("b.example", {})


def test_fixture_files_contain_no_secret_looking_strings() -> None:
    import re

    pattern = re.compile(
        r"(sk|pk|rk)_(live|test)_[A-Za-z0-9]{8,}|pat-[a-z]{2}\d?-[0-9a-f-]{20,}|sk-or-v1-[0-9a-f]{20,}|AKIA[0-9A-Z]{16}"
    )
    for path in Path(FIXTURES).rglob("*"):
        if path.is_file():
            assert not pattern.search(path.read_text(errors="ignore")), path
