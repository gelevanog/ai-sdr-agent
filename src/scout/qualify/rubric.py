"""Qualification, deterministic rules first: every point has a reason and the fact or signal it rests on.

Order of evaluation:
  1. hard disqualifiers (excluded segment, competitor, existing customer, below the hard size floors, outside the
     sales regions, marked do-not-contact): the account is disqualified, whatever else is true;
  2. not enough information (robots.txt blocked the crawl, fewer than `min_facts_for_decision` facts): nurture,
     flagged for manual research, never qualified on a guess;
  3. firmographic points (segment, employees, fleet, region) and current signals (capped), stale signals listed
     with zero points and the reason;
  4. optionally a bounded LLM adjustment (judge.py), which can move the score by at most ±llm_adjustment_max and
     never across a disqualifier.
"""

from __future__ import annotations

from scout.icp import Config
from scout.models import CompanyProfile, Qualification, Route, RubricLine, Signal


def _matches(text: str | None, needles: list[str]) -> bool:
    low = (text or "").lower()
    return any(needle.lower() in low for needle in needles)


def route_for(score: int, config: Config) -> Route:
    if score >= config.rubric.routes.qualified:
        return "qualified"
    if score >= config.rubric.routes.nurture:
        return "nurture"
    return "disqualified"


def disqualifiers(
    profile: CompanyProfile, config: Config, *, do_not_contact: bool = False
) -> list[tuple[str, list[str]]]:
    icp, seller = config.icp, config.seller
    found: list[tuple[str, list[str]]] = []
    seg = profile.fact("segment")
    if do_not_contact:
        found.append(("marked do-not-contact", []))
    if profile.segment in icp.excluded_segments:
        found.append((f"segment '{profile.segment}' is excluded", [seg.id] if seg else []))
    if profile.segment == "telematics_vendor" or _matches(profile.name, seller.competitors):
        found.append(("a competitor", [seg.id] if seg else []))
    if _matches(profile.name, seller.customers):
        found.append((f"already a {seller.company} customer", []))
    employees = profile.fact("employees")
    if employees and employees.number is not None and employees.number < icp.employees.hard_min:
        found.append((f"{employees.number} employees, below the floor of {icp.employees.hard_min}", [employees.id]))
    fleet = profile.fact("fleet_size")
    if fleet and fleet.number is not None and fleet.number < icp.fleet.hard_min:
        found.append((f"{fleet.number} vehicles, below the floor of {icp.fleet.hard_min}", [fleet.id]))
    country = profile.fact("country")
    if country and country.value not in icp.regions.allowed:
        found.append((f"based in {country.value}, outside the sales regions", [country.id]))
    return found


def _signal_points(signal: Signal, config: Config) -> tuple[int, str]:
    """Points for one signal (0 with the reason when it does not count)."""
    weights, sig_cfg, seller = config.rubric.signals, config.icp.signals, config.seller
    weight = weights.get(signal.type, 0)
    window = sig_cfg.windows_days.get(signal.type, 365)
    if not signal.current:
        if signal.age_days is None:
            return 0, "no date on the page, so its freshness is unknown"
        if signal.age_days < 0:
            return 0, f"dated {signal.date} (in the future)"
        return 0, f"{signal.age_days} days old ({signal.date}); the window is {window} days"
    when = f"{signal.age_days} days ago ({signal.date})" if signal.age_days is not None else "undated"
    if signal.type == "hiring":
        if not _matches(signal.detail or signal.citation.quote, sig_cfg.fleet_roles):
            return 0, f"'{signal.detail}' is not a fleet-related role"
        return weight, f"open role '{signal.detail}', posted {when}"
    if signal.type == "leadership_change":
        if not _matches(signal.detail or signal.citation.quote, config.rubric.leadership_titles):
            return 0, f"'{signal.detail}' is not an operations or fleet leader"
        return weight, f"new leader: {signal.detail}, {when}"
    if signal.type == "tech_stack":
        if not _matches(signal.detail or signal.citation.quote, seller.integrations):
            return 0, f"{signal.detail} is not one of {seller.company}'s integrations"
        return weight, f"uses {signal.detail}, which {seller.company} integrates with"
    if signal.type == "competitor_in_use":
        return weight, f"already uses {signal.detail or 'a competing product'} (contract lock-in likely)"
    return weight, f"{signal.type.replace('_', ' ')} {when}: {signal.summary[:120]}"


def score_rules(profile: CompanyProfile, config: Config, *, do_not_contact: bool = False) -> Qualification:
    icp, rubric = config.icp, config.rubric
    lines: list[RubricLine] = []
    dq = disqualifiers(profile, config, do_not_contact=do_not_contact)

    # ---- firmographics
    fw = rubric.firmographic
    seg = profile.fact("segment")
    if profile.segment in icp.target_segments:
        lines.append(
            RubricLine(
                criterion="Industry",
                points=fw["industry"],
                max_points=fw["industry"],
                reason=f"segment '{profile.segment}' is a target segment",
                evidence=[seg.id] if seg else [],
            )
        )
    else:
        reason = (
            f"segment '{profile.segment}' is not a target"
            if profile.segment
            else "the site does not say what the company does"
        )
        lines.append(
            RubricLine(
                criterion="Industry",
                points=0,
                max_points=fw["industry"],
                reason=reason,
                evidence=[seg.id] if seg else [],
            )
        )

    emp = profile.fact("employees")
    lo, hi = icp.employees.min, icp.employees.max or 10**9
    if emp and emp.number is not None:
        ok = lo <= emp.number <= hi
        lines.append(
            RubricLine(
                criterion="Company size",
                points=fw["employees"] if ok else 0,
                max_points=fw["employees"],
                reason=f"{emp.number:,} employees ({'within' if ok else 'outside'} {lo:,}-{hi:,})",
                evidence=[emp.id],
            )
        )
    else:
        lines.append(
            RubricLine(
                criterion="Company size", points=0, max_points=fw["employees"], reason="employee count not stated"
            )
        )

    fleet = profile.fact("fleet_size")
    if fleet and fleet.number is not None:
        ok = fleet.number >= icp.fleet.min
        lines.append(
            RubricLine(
                criterion="Fleet size",
                points=fw["fleet"] if ok else 0,
                max_points=fw["fleet"],
                reason=f"{fleet.number:,} vehicles ({'at least' if ok else 'fewer than'} {icp.fleet.min})",
                evidence=[fleet.id],
            )
        )
    else:
        lines.append(
            RubricLine(criterion="Fleet size", points=0, max_points=fw["fleet"], reason="fleet size not stated")
        )

    country = profile.fact("country")
    if country:
        ok = country.value in icp.regions.allowed
        lines.append(
            RubricLine(
                criterion="Region",
                points=fw["region"] if ok else 0,
                max_points=fw["region"],
                reason=f"based in {country.value}" + ("" if ok else ", outside the sales regions"),
                evidence=[country.id],
            )
        )
    else:
        lines.append(RubricLine(criterion="Region", points=0, max_points=fw["region"], reason="country not stated"))

    # ---- signals: the best signal of each type counts once; stale ones are listed with zero points
    positive_total = 0
    negative_total = 0
    counted_types: set[str] = set()
    for signal in sorted(
        profile.signals, key=lambda s: (not s.current, s.age_days if s.age_days is not None else 10**6)
    ):
        points, reason = _signal_points(signal, config)
        if points and signal.type in counted_types:
            points, reason = 0, f"another {signal.type.replace('_', ' ')} signal already counted"
        if points:
            counted_types.add(signal.type)
        if points > 0:
            room = max(0, rubric.signals_cap - positive_total)
            if points > room:
                reason += f" (capped: signals add at most {rubric.signals_cap})"
                points = room
            positive_total += points
        else:
            negative_total += points
        label = signal.type.replace("_", " ").capitalize()
        lines.append(
            RubricLine(
                criterion=f"Signal: {label}",
                points=points,
                max_points=max(0, rubric.signals.get(signal.type, 0)),
                reason=reason,
                evidence=[signal.id],
            )
        )

    score = max(0, min(100, sum(line.points for line in lines)))
    rules_score = score
    if dq:
        route: Route = "disqualified"
        score = 0
    elif profile.blocked_by_robots or len(profile.facts) < rubric.min_facts_for_decision:
        route = "nurture"
        why = "robots.txt disallows crawling" if profile.blocked_by_robots else f"only {len(profile.facts)} facts found"
        lines.append(
            RubricLine(
                criterion="Data sufficiency",
                points=0,
                max_points=0,
                reason=f"not enough public information ({why}); needs manual research",
            )
        )
    else:
        route = route_for(score, config)
    return Qualification(
        score=score,
        route=route,
        lines=lines,
        disqualifiers=[reason for reason, _ in dq],
        rules_score=rules_score,
        mode="rules_first",
    )
