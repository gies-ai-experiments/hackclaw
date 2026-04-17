"""Tests for the simple ``Subject:``-prefixed template renderer."""
from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.onboard.templates import RenderedEmail, load_template, render


def test_render_substitutes_name() -> None:
    raw = "Subject: Hi {name}\n\nDear {name}, welcome.\n"
    out = render(raw, name="Alice")
    assert isinstance(out, RenderedEmail)
    assert out.subject == "Hi Alice"
    assert out.body == "Dear Alice, welcome.\n"


def test_render_requires_subject_line() -> None:
    with pytest.raises(ValueError):
        render("No subject line here\nBody", name="X")


def test_load_template_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "t.txt"
    p.write_text("Subject: Hello {name}\n\nBody for {name}.\n")
    raw = load_template(p)
    out = render(raw, name="Bob")
    assert out.subject == "Hello Bob"
    assert out.body == "Body for Bob.\n"
