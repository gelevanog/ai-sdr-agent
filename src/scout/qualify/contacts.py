"""Contact selection: the first persona (in the ICP's order) with a matching person and a published business email.

Synthetic mode reads people from the company's team page (fictional people). Live mode never takes people from
websites: contacts come only from a list the client supplies (CSV with domain, name, title, email). A person
without a printed or supplied address is skipped with a note; Scout never guesses an address. Suppressed and
do-not-contact addresses are skipped everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from scout.icp import Config
from scout.models import CompanyProfile, Contact


@dataclass(frozen=True)
class ClientContact:
    domain: str
    name: str
    title: str
    email: str


@dataclass
class Selection:
    contact: Contact | None
    notes: list[str] = field(default_factory=list)


def _match(title: str, needles: list[str]) -> bool:
    low = f" {title.lower()} "
    return any(f" {needle.lower()} " in low or needle.lower() == title.lower().strip() for needle in needles)


def persona_of(title: str, config: Config) -> str | None:
    if _match(title, config.never_contact_titles):
        return None
    for persona in config.personas:
        if _match(title, persona.titles):
            return persona.id
    return None


def select_contact(
    profile: CompanyProfile,
    config: Config,
    *,
    suppressed: set[str] | None = None,
    client_contacts: list[ClientContact] | None = None,
) -> Selection:
    suppressed = {e.lower() for e in (suppressed or set())}
    notes: list[str] = []
    candidates: list[tuple[int, Contact]] = []
    order = {p.id: i for i, p in enumerate(config.personas)}
    if profile.mode == "live" or client_contacts:
        for cc in client_contacts or []:
            if cc.domain.lower() != profile.domain.lower():
                continue
            persona = persona_of(cc.title, config)
            if persona is None:
                notes.append(f"{cc.name} ({cc.title}): not a target persona")
                continue
            contact = Contact(name=cc.name, title=cc.title, email=cc.email.lower(), persona=persona, source="client_list")
            candidates.append((order[persona], contact))
    else:
        for person in profile.people:
            persona = persona_of(person.title, config)
            if persona is None:
                continue
            if not person.email:
                notes.append(f"{person.name} ({person.title}): no published email address; not guessed")
                continue
            contact = Contact(
                name=person.name, title=person.title, email=person.email, persona=persona, source="team_page", citation=person.citation
            )
            candidates.append((order[persona], contact))
    for _, contact in sorted(candidates, key=lambda item: item[0]):
        if contact.email and contact.email.lower() in suppressed:
            notes.append(f"{contact.name}: on the suppression list")
            continue
        contact.reason = f"{contact.title} matches the '{contact.persona}' persona"
        return Selection(contact=contact, notes=notes)
    if any("team" in url and reason == "robots.txt" for url, reason in profile.skipped):
        notes.append("the team page is disallowed by robots.txt, so it was not read; ask the client for a contact")
    if not candidates:
        notes.append("no person on the site matches a target persona" if profile.mode == "synthetic" else "no client-supplied contact for this domain")
    return Selection(contact=None, notes=notes)
