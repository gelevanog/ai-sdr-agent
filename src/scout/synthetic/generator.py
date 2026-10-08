"""Renders the company specs into static websites (one folder per domain) with a seeded RNG.

Same specs + same seed = byte-identical output (checked by a test and by `manifest.json`). The planted sentences
from the specs are rendered verbatim, so the evaluation can check that a quoted citation really is on the page.
Each site has: index (about), services, pricing, careers, news, press, team, sometimes a blog, and robots.txt.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import random
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from scout.synthetic import content as C  # noqa: N812 - short alias for the wording pools
from scout.synthetic.spec import CompanySpec, SpecSignal

REFERENCE_DATE = dt.date(2026, 10, 1)
PAGES = ["index", "services", "pricing", "careers", "news", "press", "team"]
PAGE_FILE = {
    "about": "index.html",
    "services": "services.html",
    "pricing": "pricing.html",
    "careers": "careers.html",
    "news": "news.html",
    "press": "press.html",
    "team": "team.html",
    "blog": "blog.html",
}


@dataclass(frozen=True)
class TeamMember:
    name: str
    title: str
    email: str | None
    bio: str


@dataclass
class Site:
    spec: CompanySpec
    pages: dict[str, str] = field(default_factory=dict)
    """File name -> HTML."""
    robots: str = ""
    team: list[TeamMember] = field(default_factory=list)


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def email_for(name: str, domain: str) -> str:
    parts = _ascii(name).lower().replace("'", "").split()
    return f"{parts[0]}.{parts[-1].replace(' ', '')}@{domain}"


def _fmt_date(day: dt.date) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


def _e(text: str) -> str:
    return html.escape(text, quote=True)


class _Writer:
    def __init__(self, spec: CompanySpec, seed: int) -> None:
        self.spec = spec
        self.rng = random.Random(f"{seed}:{spec.domain}")
        self.fleet = spec.segment in C.FLEET_SEGMENTS
        self.city = spec.hq.split(",")[0]
        self.country_name = C.COUNTRY_NAMES.get(spec.country, spec.country)
        self.accent = self.rng.choice(C.ACCENTS)
        self.street = f"{self.rng.randint(10, 990)} {self.rng.choice(C.STREETS)}"

    # ------------------------------------------------------------------ helpers
    def _recent(self, lo_days: int = 20, hi_days: int = 330, before: dt.date | None = None) -> dt.date:
        anchor = before or REFERENCE_DATE
        return anchor - dt.timedelta(days=self.rng.randint(lo_days, hi_days))

    def _updated(self, page: str) -> dt.date:
        return self.spec.outdated.get(page) or (REFERENCE_DATE - dt.timedelta(days=self.rng.randint(3, 25)))

    def _signals(self, page: str, *types: str) -> list[SpecSignal]:
        planted = [*self.spec.signals, *self.spec.decoys]
        return [s for s in planted if s.page == page and (not types or s.type in types)]

    def _layout(self, page: str, title: str, body: str) -> str:
        s = self.spec
        nav = [
            ("index.html", "About"),
            ("services.html", "Services"),
            ("pricing.html", "Pricing"),
            ("careers.html", "Careers"),
            ("news.html", "News"),
            ("press.html", "Press"),
            ("team.html", "Team"),
        ]
        if self._has_blog():
            nav.append(("blog.html", "Blog"))
        links = " ".join(f'<a href="{href}">{label}</a>' for href, label in nav)
        updated = self._updated(page)
        return (
            "<!doctype html>\n"
            '<html lang="en"><head><meta charset="utf-8">'
            f"<title>{_e(title)} | {_e(s.name)}</title>"
            f'<meta name="description" content="{_e(s.name)}: {_e(s.industry)} in {_e(s.hq)}.">'
            f'<meta name="last-modified" content="{updated.isoformat()}">'
            "<style>body{font-family:Georgia,serif;max-width:860px;margin:0 auto;padding:24px;color:#1f2933}"
            f"header{{border-bottom:3px solid {self.accent};padding-bottom:12px}}nav a{{margin-right:14px;color:{self.accent}}}"
            "article{margin:18px 0}time{color:#52606d}.job{border:1px solid #d9e2ec;padding:12px;margin:10px 0}"
            "footer{margin-top:40px;font-size:13px;color:#52606d;border-top:1px solid #d9e2ec;padding-top:10px}</style>"
            "</head><body>"
            f'<header><strong style="font-size:22px">{_e(s.name)}</strong><nav>{links}</nav></header>'
            f"<main><h1>{_e(title)}</h1>\n{body}\n</main>"
            f"<footer><p>{_e(s.name)} · {self.street}, {_e(s.hq)}, {_e(self.country_name)}</p>"
            f'<p>Last updated <time datetime="{updated.isoformat()}">{_fmt_date(updated)}</time></p></footer>'
            "</body></html>\n"
        )

    def _has_blog(self) -> bool:
        return bool(self.spec.benign_ai_text) or "blog" in self.spec.outdated or bool(self._signals("blog"))

    # ------------------------------------------------------------------ pages
    def about(self) -> str:
        s = self.spec
        tagline = self.rng.choice(C.TAGLINES["fleet" if self.fleet else "other"]).format(
            served=s.served, founded=s.founded
        )
        intro_options = [
            f"{s.name} is a {s.industry} company headquartered in {s.hq}, serving {s.served}.",
            f"Headquartered in {s.hq}, {s.name} provides {s.industry} to customers across {s.served}.",
        ]
        people_options = [
            f"Founded in {s.founded}, we employ about {s.employees:,} people.",
            f"Since {s.founded} we have grown to a team of roughly {s.employees:,} employees.",
            f"Our team of {s.employees:,} people has been at it since {s.founded}.",
        ]
        paras = [f"<p><em>{_e(tagline)}</em></p>", f"<p>{_e(self.rng.choice(intro_options))}</p>"]
        sentences = [self.rng.choice(people_options)]
        if s.fleet_size is not None:
            fleet_options = [
                f"We operate a fleet of {s.fleet_size:,} {s.fleet_noun}.",
                f"Our {s.fleet_size:,} {s.fleet_noun} are on the road every working day.",
                f"Customers are served by a fleet of {s.fleet_size:,} {s.fleet_noun}, maintained in house.",
            ]
            sentences.append(self.rng.choice(fleet_options))
        if s.bike_fleet:
            sentences.append(f"Our fleet of {s.bike_fleet:,} rental bikes is serviced every night.")
        if s.segment == "restaurants":
            sentences.append("Delivery is handled by the major delivery apps; we do not run our own vehicles.")
        if s.segment == "hardware":
            sentences.append("We do not operate delivery vehicles: carriers drop parcels into our lockers.")
        paras.append(f"<p>{_e(' '.join(sentences))}</p>")
        for sig in self._signals("about"):
            paras.append(f"<p>{_e(sig.text)}</p>")
        if s.injection and s.injection.page == "about":
            paras.append(self._injection_html())
        values = self.rng.sample(C.VALUES, 3)
        paras.append("<h2>How we work</h2><ul>" + "".join(f"<li>{_e(v)}</li>" for v in values) + "</ul>")
        return self._layout("about", f"About {s.name}", "\n".join(paras))

    def _injection_html(self) -> str:
        assert self.spec.injection is not None
        text = _e(self.spec.injection.text)
        if self.spec.injection.style == "hidden":
            return f'<div style="display:none" aria-hidden="true">{text}</div>'
        return f"<p>{text}</p>"

    def services(self) -> str:
        s = self.spec
        items = C.SERVICES.get(s.segment, ["Consulting", "Support"])
        picked = self.rng.sample(items, min(len(items), self.rng.randint(3, len(items))))
        body = ["<p>What we do for our customers:</p>", "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in picked) + "</ul>"]
        for sig in self._signals("services"):
            body.append(f"<p>{_e(sig.text)}</p>")
        return self._layout("services", "Services", "\n".join(body))

    def pricing(self) -> str:
        kind = (
            "fleet"
            if self.fleet
            else ("software" if self.spec.segment in {"software", "telematics_vendor"} else "other")
        )
        lines = C.PRICING[kind]
        body = "<ul>" + "".join(f"<li>{_e(line)}</li>" for line in lines) + "</ul>"
        return self._layout("pricing", "Pricing", body)

    def careers(self) -> str:
        s = self.spec
        outdated = s.outdated.get("careers")
        jobs: list[tuple[str, dt.date, str]] = []
        for sig in self._signals("careers", "hiring"):
            assert sig.date is not None
            jobs.append((sig.text, sig.date, self.rng.choice(C.FLEET_JOB_BLURBS)))
        tech_lines = [sig.text for sig in self._signals("careers", "tech_stack")]
        noise = self.rng.sample(C.NOISE_JOBS.get(s.segment, ["Office Assistant"]), 2) + list(s.extra_jobs)
        for i, title in enumerate(noise):
            day = self._recent(5, 120, before=outdated) if outdated else self._recent(5, 120)
            blurb = self.rng.choice(C.JOB_BLURBS)
            if i == 0 and tech_lines:
                blurb = f"{blurb} {' '.join(tech_lines)}"
            jobs.append((title, day, blurb))
        jobs.sort(key=lambda j: (j[1], j[0]), reverse=True)
        cards = []
        for title, day, blurb in jobs:
            cards.append(
                f'<div class="job"><h3>{_e(title)}</h3>'
                f'<p>{_e(self.city)} · Posted <time datetime="{day.isoformat()}">{_fmt_date(day)}</time></p>'
                f"<p>{_e(blurb)}</p></div>"
            )
        intro = f"<p>Join {_e(s.name)}. We are an equal-opportunity employer.</p>"
        return self._layout("careers", "Careers", intro + "\n" + "\n".join(cards))

    def _dated_items(self, page: str, types: tuple[str, ...], filler: list[str], n_filler: int) -> str:
        s = self.spec
        outdated = s.outdated.get(page)
        items: list[tuple[dt.date, str]] = []
        for sig in self._signals(page, *types):
            items.append((sig.date or self._recent(30, 200, before=outdated), sig.text))
        for text in self.rng.sample(filler, n_filler):
            day = self._recent(40, 500, before=outdated) if outdated else self._recent(15, 330)
            items.append((day, text.format(name=s.name, city=self.city)))
        items.sort(key=lambda it: (it[0], it[1]), reverse=True)
        parts = [
            f'<article><time datetime="{day.isoformat()}">{_fmt_date(day)}</time><p>{_e(text)}</p></article>'
            for day, text in items
        ]
        if s.injection and s.injection.page == page:
            parts.insert(1 if parts else 0, self._injection_html())
        return "\n".join(parts)

    def news(self) -> str:
        body = self._dated_items("news", ("expansion", "tech_stack", "funding", "competitor_in_use"), C.FILLER_NEWS, 2)
        return self._layout("news", "News", body)

    def press(self) -> str:
        body = self._dated_items("press", ("funding", "leadership_change", "expansion"), C.FILLER_PRESS, 1)
        return self._layout("press", "Press", body)

    def blog(self) -> str:
        s = self.spec
        posts: list[tuple[dt.date, str, str]] = []
        outdated = s.outdated.get("blog")
        if s.benign_ai_text:
            posts.append((self._recent(10, 60), "What a morning briefing looks like now", s.benign_ai_text))
        posts.append(
            (
                self._recent(30, 300, before=outdated) if outdated else self._recent(30, 300),
                "Five things we learned this season",
                f"Our crews in {self.city} share what worked and what did not.",
            )
        )
        posts.sort(key=lambda p: p[0], reverse=True)
        body = "\n".join(
            f'<article><h2>{_e(t)}</h2><time datetime="{d.isoformat()}">{_fmt_date(d)}</time><p>{_e(x)}</p></article>'
            for d, t, x in posts
        )
        return self._layout("blog", "Blog", body)

    def build_team(self) -> list[TeamMember]:
        s = self.spec
        locale = C.LOCALE_BY_COUNTRY.get(s.country, "en")
        used: set[str] = {sig.person for sig in s.signals if sig.person}

        def person() -> str:
            for _ in range(50):
                name = f"{self.rng.choice(C.FIRST_NAMES[locale])} {self.rng.choice(C.LAST_NAMES[locale])}"
                if name not in used:
                    used.add(name)
                    return name
            raise RuntimeError("name pool exhausted")

        leaders: dict[str, str] = {
            sig.title: sig.person for sig in s.signals if sig.type == "leadership_change" and sig.person and sig.title
        }
        hiring = " ".join((sig.title or sig.text).lower() for sig in s.signals if sig.type == "hiring" and sig.current)
        roles: list[tuple[str, float]] = []
        ceo_title = (
            "Managing Director"
            if s.country in {"DE", "AT", "GB"} and self.rng.random() < 0.5
            else "Chief Executive Officer"
        )
        roles.append((ceo_title, 0.5))
        if self.fleet:
            ops = self.rng.choice(
                ["VP Operations", "Director of Operations", "Chief Operating Officer", "Operations Director"]
            )
            roles.append((ops, 0.85))
            fleet_leader_hired = any("fleet" in t.lower() for t in leaders)
            if not fleet_leader_hired and not any(
                k in hiring for k in ("fleet manager", "head of fleet", "transport manager")
            ):
                roles.append((self.rng.choice(["Fleet Manager", "Head of Fleet", "Fleet Operations Manager"]), 0.85))
            if s.segment in {"passenger_transport", "freight_trucking", "waste_management"}:
                roles.append(("Safety Director", 0.6))
        else:
            roles.append((self.rng.choice(["Chief Technology Officer", "Creative Director", "Head of Product"]), 0.5))
        roles += [("Chief Financial Officer", 0.4), ("HR Director", 0.5), ("Marketing Manager", 0.5)]
        team: list[TeamMember] = []
        replaced: set[str] = set()
        for title, p_email in roles:
            name = None
            for leader_title, leader in leaders.items():
                same_seat = (
                    leader_title == title
                    or (
                        leader_title in {"Chief Operating Officer", "Director of Operations"}
                        and title
                        in {"VP Operations", "Director of Operations", "Chief Operating Officer", "Operations Director"}
                    )
                    or (leader_title in {"Chief Executive Officer", "Managing Director"} and title == ceo_title)
                )
                if same_seat and leader_title not in replaced:
                    name, title = leader, leader_title
                    replaced.add(leader_title)
                    break
            name = name or person()
            email = email_for(name, s.domain) if self.rng.random() < p_email else None
            team.append(TeamMember(name=name, title=title, email=email, bio=self._bio(title)))
        for leader_title, leader in leaders.items():
            if leader_title not in replaced:  # e.g. "Head of Fleet", "VP Fleet Operations"
                team.insert(1, TeamMember(leader, leader_title, email_for(leader, s.domain), self._bio(leader_title)))
        return team

    def _bio(self, title: str) -> str:
        years = self.rng.randint(4, 22)
        prior = self.rng.choice(["operations", "logistics", "field service", "finance", "people management", "sales"])
        return f"{years} years of experience in {prior} before joining as {title}."

    def team_page(self, team: list[TeamMember]) -> str:
        cards = []
        for m in team:
            contact = f'<a href="mailto:{m.email}">{m.email}</a>' if m.email else "Contact via our main office"
            cards.append(
                f'<div class="person"><h3>{_e(m.name)}</h3><p class="title">{_e(m.title)}</p>'
                f"<p>{_e(m.bio)}</p><p>{contact}</p></div>"
            )
        return self._layout("team", "Our team", "\n".join(cards))

    def robots(self) -> str:
        if self.spec.robots == "disallow_all":
            return "User-agent: *\nDisallow: /\n"
        if self.spec.robots == "disallow_team":
            return "User-agent: *\nDisallow: /team.html\n"
        return "User-agent: *\nDisallow: /internal/\n"


def render_site(spec: CompanySpec, seed: int) -> Site:
    w = _Writer(spec, seed)
    site = Site(spec=spec)
    site.team = w.build_team()
    site.pages = {
        "index.html": w.about(),
        "services.html": w.services(),
        "pricing.html": w.pricing(),
        "careers.html": w.careers(),
        "news.html": w.news(),
        "press.html": w.press(),
        "team.html": w.team_page(site.team),
    }
    if w._has_blog():
        site.pages["blog.html"] = w.blog()
    site.robots = w.robots()
    return site


def write_web(specs: list[CompanySpec], out_dir: Path, seed: int) -> dict[str, str]:
    """Writes every site under out_dir/<domain>/ and returns {relative path: sha256} (also saved as manifest.json)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for child in out_dir.iterdir():  # clear the contents, not the directory (it may be a mounted volume)
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    manifest: dict[str, str] = {}
    index_links = []
    for spec in specs:
        site = render_site(spec, seed)
        root = out_dir / spec.domain
        root.mkdir()
        files = {**site.pages, "robots.txt": site.robots}
        for name, text in files.items():
            (root / name).write_text(text, encoding="utf-8")
            manifest[f"{spec.domain}/{name}"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        index_links.append(
            f'<li><a href="{spec.domain}/index.html">{_e(spec.name)}</a> <code>{spec.domain}</code></li>'
        )
    (out_dir / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Scout synthetic web</title></head><body>"
        "<h1>Scout synthetic web</h1><p>Fictional companies generated by <code>scout seed</code>. Nothing here is a "
        "real organisation or person.</p><ul>" + "".join(index_links) + "</ul></body></html>\n",
        encoding="utf-8",
    )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return manifest
