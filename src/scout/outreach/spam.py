"""Deliverability and tone checks: spam-trigger words, length, readability, subject line, links, shouting,
placeholders left in the text, and the offer's banned phrases."""

from __future__ import annotations

import re

from scout.models import CheckIssue, Email

SPAM_WORDS = (
    "free", "guarantee", "guaranteed", "risk-free", "act now", "limited time", "urgent", "winner", "100%",
    "no obligation", "click here", "buy now", "cash", "earn money", "double your", "best price", "amazing",
    "incredible", "once in a lifetime", "exclusive deal", "special promotion", "don't miss",
)  # fmt: skip
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_PLACEHOLDER = re.compile(r"\{\{?\s*\w+\s*\}?\}|\[(?:first ?name|company|name|title)\]", re.I)
_LINK = re.compile(r"https?://|www\.", re.I)


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def _syllables(word: str) -> int:
    w = word.lower().strip("'")
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def flesch_reading_ease(text: str) -> float:
    sentences = max(1, len([s for s in re.split(r"[.!?]+", text) if s.strip()]))
    ws = words(text)
    if not ws:
        return 0.0
    syllables = sum(_syllables(w) for w in ws)
    return round(206.835 - 1.015 * (len(ws) / sentences) - 84.6 * (syllables / len(ws)), 1)


def check_email(
    email: Email, *, max_words: int, banned_phrases: list[str]
) -> tuple[list[CheckIssue], float, float, int]:
    """Returns (issues, spam score 0-1, reading ease, word count)."""
    issues: list[CheckIssue] = []
    body = email.body
    low = f" {body.lower()} "
    hits = [w for w in SPAM_WORDS if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low)]
    for word in hits:
        issues.append(
            CheckIssue(
                step=email.step, kind="spam_word", message=f"spam-trigger phrase '{word}'", severity="warn", text=word
            )
        )
    for phrase in banned_phrases:
        if phrase.lower() in low:
            issues.append(
                CheckIssue(step=email.step, kind="banned_phrase", message=f"banned phrase '{phrase}'", text=phrase)
            )
    n_words = len(words(body))
    if n_words > max_words * 1.25:
        issues.append(CheckIssue(step=email.step, kind="length", message=f"{n_words} words; the limit is {max_words}"))
    elif n_words > max_words:
        issues.append(
            CheckIssue(
                step=email.step, kind="length", message=f"{n_words} words; the target is {max_words}", severity="warn"
            )
        )
    if n_words < 25:
        issues.append(CheckIssue(step=email.step, kind="length", message=f"only {n_words} words", severity="warn"))
    ease = flesch_reading_ease(body)
    if ease < 40:
        issues.append(
            CheckIssue(
                step=email.step,
                kind="readability",
                message=f"hard to read (Flesch reading ease {ease})",
                severity="warn",
            )
        )
    if _PLACEHOLDER.search(body) or _PLACEHOLDER.search(email.subject):
        issues.append(CheckIssue(step=email.step, kind="placeholder", message="template placeholder left in the text"))
    if _LINK.search(body):
        issues.append(
            CheckIssue(
                step=email.step, kind="links", message="links in the body (Scout adds only the unsubscribe link)"
            )
        )
    if body.count("!") + email.subject.count("!") > 1:
        issues.append(CheckIssue(step=email.step, kind="exclamation", message="more than one exclamation mark"))
    caps = [
        w
        for w in words(body + " " + email.subject)
        if len(w) >= 4 and w.isupper() and w not in {"SAP", "COO", "CEO", "HVAC", "NASA"}
    ]
    if caps:
        issues.append(
            CheckIssue(step=email.step, kind="caps", message=f"shouting: {', '.join(caps[:3])}", severity="warn")
        )
    if email.step == 1 and not email.subject.strip():
        issues.append(CheckIssue(step=email.step, kind="subject", message="empty subject"))
    if len(email.subject) > 60:
        issues.append(
            CheckIssue(
                step=email.step, kind="subject", message=f"subject is {len(email.subject)} characters", severity="warn"
            )
        )
    if email.subject and email.subject.isupper():
        issues.append(CheckIssue(step=email.step, kind="subject", message="subject in capitals"))
    spam_score = min(
        1.0, 0.25 * len(hits) + 0.3 * bool(_LINK.search(body)) + 0.2 * (body.count("!") > 1) + 0.15 * len(caps)
    )
    return issues, round(spam_score, 2), ease, n_words
