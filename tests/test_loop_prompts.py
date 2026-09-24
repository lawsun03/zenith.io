"""Prompt template loading/hashing (research/loop/prompts.py)."""
from __future__ import annotations

from research.loop.prompts import load_template


def test_load_template_hashes_raw_unrendered_text():
    template = load_template("triage_v1.md")
    assert template.sha256
    assert "{sessions_json}" in template.text  # unrendered — placeholder still literal


def test_load_template_is_deterministic():
    a = load_template("triage_v1.md")
    b = load_template("triage_v1.md")
    assert a.sha256 == b.sha256


def test_different_templates_hash_differently():
    a = load_template("triage_v1.md")
    b = load_template("hypothesis_v1.md")
    assert a.sha256 != b.sha256


def test_render_substitutes_placeholders_without_changing_hash():
    template = load_template("triage_v1.md")
    rendered = template.render(max_shortlist=5, sessions_json="[]")
    assert "{sessions_json}" not in rendered
    assert "[]" in rendered
    assert template.sha256 == load_template("triage_v1.md").sha256  # hash is of the file, not the render


def test_all_four_stage_templates_load():
    for name in (
        "triage_v1.md", "hypothesis_v1.md", "adversarial_review_v1.md", "variants_v1.md",
    ):
        template = load_template(name)
        assert template.text
        assert len(template.sha256) == 64
