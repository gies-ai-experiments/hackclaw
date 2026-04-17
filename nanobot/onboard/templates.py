"""Tiny ``Subject:``-prefixed plaintext email template renderer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RenderedEmail:
    """A rendered email ready to hand to ``EmailMessage``."""

    subject: str
    body: str


def load_template(path: Path) -> str:
    """Read the template file and return its raw text."""
    return Path(path).read_text(encoding="utf-8")


def render(raw: str, *, name: str) -> RenderedEmail:
    """Split the ``Subject:`` line off *raw* and substitute ``{name}``.

    The template **must** start with ``Subject: <text>`` followed by a
    blank line. Anything after that blank line is the body. Both subject
    and body have ``{name}`` placeholders replaced.
    """
    lines = raw.splitlines(keepends=False)
    if not lines or not lines[0].lower().startswith("subject:"):
        raise ValueError("Template must start with a 'Subject:' line")
    subject = lines[0].split(":", 1)[1].strip()
    # Skip the subject line and the (required) blank line after it.
    body_start = 1
    if body_start < len(lines) and lines[body_start] == "":
        body_start += 1
    body = "\n".join(lines[body_start:])
    if raw.endswith("\n") and not body.endswith("\n"):
        body += "\n"
    subject = subject.format(name=name)
    body = body.format(name=name)
    return RenderedEmail(subject=subject, body=body)
