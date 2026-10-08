"""API endpoints with the offline model and an in-memory mailer. Needs TEST_DATABASE_URL."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from scout.api.app import create_app
from scout.services import Scout

pytestmark = pytest.mark.db


@pytest.fixture
def client(pipeline: Scout) -> Iterator[TestClient]:
    with TestClient(create_app(pipeline)) as c:
        yield c


def test_health_overview_and_accounts(client: TestClient) -> None:
    assert client.get("/health").json()["ok"]
    overview = client.get("/api/overview").json()
    assert overview["funnel"]["qualified"] == 22 and overview["capture_only"] is True
    accounts = client.get("/api/accounts").json()
    assert len(accounts) == 60 and accounts[0]["score"] >= accounts[-1]["score"]
    assert {a["route"] for a in client.get("/api/accounts", params={"route": "qualified"}).json()} == {"qualified"}


def test_dossier_and_source(client: TestClient) -> None:
    acc = next(a for a in client.get("/api/accounts").json() if a["domain"] == "brightwater-fs.example")
    dossier = client.get(f"/api/accounts/{acc['id']}").json()
    profile = dossier["account"]["profile"]
    assert profile["facts"] and profile["signals"] and dossier["pages"]
    fact = profile["facts"][0]
    source = client.get(
        "/api/source",
        params={"account_id": acc["id"], "url": fact["citation"]["url"], "quote": fact["citation"]["quote"]},
    ).json()
    assert source["quote_found"] is True
    assert client.get("/api/accounts/9999").status_code == 404


def test_review_flow(client: TestClient, pipeline: Scout) -> None:
    queue = client.get("/api/drafts").json()
    assert queue and all(d["status"] == "pending" for d in queue)
    draft_id = queue[0]["id"]
    detail = client.get(f"/api/drafts/{draft_id}").json()
    assert detail["profile"]["facts"] and len(detail["draft"]["data"]["emails"]) == 3
    email = detail["draft"]["data"]["emails"][0]
    approved = client.post(
        f"/api/drafts/{draft_id}/approve",
        json={
            "reviewer": "ivan",
            "edits": [{"step": 1, "subject": email["subject"], "body": email["body"] + "\nThanks."}],
        },
    )
    assert approved.status_code == 200 and len(approved.json()["scheduled"]) == 3
    assert client.post(f"/api/drafts/{draft_id}/approve", json={"reviewer": "ivan"}).status_code == 409
    other = client.get("/api/drafts").json()[0]["id"]
    assert client.post(f"/api/drafts/{other}/reject", json={"reviewer": "ivan", "reason": "tone"}).json()["ok"]
    assert len(client.get("/api/feedback").json()) == 1
    sent = client.post("/api/send/run", json={"fast_forward_days": 20}).json()
    assert sent["sent"] == 3
    assert {m["status"] for m in client.get("/api/messages").json()} == {"sent"}


def test_replies_meetings_and_unsubscribe(client: TestClient, pipeline: Scout) -> None:
    draft_id = client.get("/api/drafts").json()[0]["id"]
    client.post(f"/api/drafts/{draft_id}/approve", json={"reviewer": "ivan"})
    client.post("/api/send/run", json={"fast_forward_days": 2})
    simulated = client.post("/api/replies/simulate").json()
    assert simulated == {"created": 1, "processed": 1}
    replies = client.get("/api/replies").json()
    assert replies[0]["label"] and replies[0]["actions"]
    token = pipeline.store.scalar("SELECT unsubscribe_token FROM messages WHERE step = 3")
    assert "Unsubscribe" in client.get(f"/u/{token}").text
    assert "unsubscribed" in client.post(f"/u/{token}").text
    assert client.post("/u/not-a-token").status_code == 404
    assert any(s["reason"] == "unsubscribed" or s["kind"] == "email" for s in client.get("/api/suppression").json())


def test_manual_reply_suppression_and_dnc(client: TestClient) -> None:
    assert (
        client.post("/api/replies", json={"from_email": "someone@nowhere.example", "body": "remove me"}).json()["label"]
        == "unsubscribe"
    )
    assert (
        client.post(
            "/api/suppression", json={"value": "rival.example", "kind": "domain", "reason": "competitor"}
        ).status_code
        == 200
    )
    acc = client.get("/api/accounts").json()[0]
    assert "cancelled" in client.post(f"/api/accounts/{acc['id']}/do-not-contact", json={"flag": True}).json()


def test_jobs_settings_audit_exports(client: TestClient) -> None:
    job = client.post("/api/accounts/1/qualify", params={"sync": True}).json()
    assert job["status"] == "done" and job["result"]["route"]
    queued = client.post("/api/accounts/2/research").json()
    assert queued["status"] == "queued" and client.get(f"/api/jobs/{queued['job_id']}").json()["status"] == "queued"
    icp = client.get("/api/settings/icp").json()
    assert "seller:" in icp["yaml"] and icp["config"]["seller"]["company"] == "Wayline"
    assert client.put("/api/settings/icp", json={"yaml": "seller: {}\n"}).status_code == 422
    assert client.put("/api/settings/icp", json={"yaml": icp["yaml"]}).json()["ok"]
    assert (
        client.put("/api/settings/compliance", json={"daily_send_cap": 5, "nonsense": 9}).json()["daily_send_cap"] == 5
    )
    assert any(a["action"] == "settings.compliance_updated" for a in client.get("/api/audit").json())
    assert client.get("/api/audit", params={"action": "account."}).json()
    csv = client.get("/api/export/accounts.csv")
    assert csv.headers["content-type"].startswith("text/csv") and csv.text.startswith("domain,")
    assert client.get("/api/export/nope.csv").status_code == 404
    assert "available" in client.get("/api/evaluation").json()
    assert client.post("/api/accounts", json={"url": "https://northwind-fleet.test"}).json()["id"]
    assert client.post("/api/accounts", json={"url": "northwind-fleet.test"}).status_code == 409
