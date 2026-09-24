"""Prompt template loading and hashing.

Templates are committed files under research/loop/prompts/, one per stage,
versioned by filename suffix (_v1, _v2, ...) — a prompt-wording change
gets a new file and a new hash, rather than silently reinterpreting old
ledger rows' prompt_hash against different text (docs/research-loop's
"prompt templates live in the repo as versioned files").

`load_template` hashes the file's raw, unrendered text — the hash
identifies which template produced a hypothesis, not the anomaly-specific
values plugged into it. Two calls that render the same template with
different data still carry the same prompt_hash, which is correct: the
prompt (the *reusable* thing) is what's versioned, not the message.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    text: str
    sha256: str

    def render(self, **kwargs: object) -> str:
        return self.text.format(**kwargs)


def load_template(filename: str) -> PromptTemplate:
    path = _PROMPTS_DIR / filename
    text = path.read_text()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return PromptTemplate(name=filename, text=text, sha256=digest)
