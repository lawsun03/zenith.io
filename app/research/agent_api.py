"""The research agents' only interface (spec §11). Slices so far: protocols
(step 3) and holdout requests (step 4).

Agents read protocols and never write them. That is enforced here, not by
convention: write calls raise PermissionError and are logged with the
agent's name and model. Holdout requests only queue; a human releases them
from a process that can read the vault. The rest of §11 (register_family,
run_dev, …) lands in build step 7.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.research.holdout import AGENT, request_holdout
from app.research.ledger import Ledger
from app.research.protocol import PROTOCOLS_DIR, verify_protocol
from app.research.vault import VAULT_ENV, assert_unreadable, vault_dir_from_env

log = logging.getLogger(__name__)


class AgentAPI:
    def __init__(self, agent_name: str, model_id: str, ledger: Ledger,
                 protocols_dir=PROTOCOLS_DIR, vault_dir: Path | None = None) -> None:
        # Spec §3: if the worker can read the vault, the setup is wrong. Refuse
        # to start rather than run research next to readable holdout bars.
        vault_dir = vault_dir or vault_dir_from_env()
        if vault_dir is None:
            log.warning("%s not set: agent %s (%s) started without the vault isolation check",
                        VAULT_ENV, agent_name, model_id)
        else:
            assert_unreadable(vault_dir)
        self.agent_name = agent_name
        self.model_id = model_id
        self._ledger = ledger
        self._protocols_dir = protocols_dir

    def get_protocol(self, protocol_id: str) -> dict:
        p = verify_protocol(protocol_id, self._ledger, self._protocols_dir)
        return {"protocol_id": p.id, "hash": p.hash, "holdout_status": p.holdout_status,
                "protocol": p.data}

    def _refuse(self, action: str, protocol_id: str) -> None:
        log.warning("agent %s (%s) refused: %s %s", self.agent_name, self.model_id,
                    action, protocol_id)
        raise PermissionError(f"agents cannot {action} protocols ({protocol_id})")

    def write_protocol(self, protocol_id: str, yaml_text: str) -> None:
        self._refuse("write", protocol_id)

    def lock_protocol(self, protocol_id: str) -> None:
        self._refuse("lock", protocol_id)

    def request_holdout(self, strategy_hash: str, protocol_id: str) -> dict:
        """Queue a one-shot holdout evaluation for human approval. Raises
        HoldoutRefused (already recorded) when the vault would refuse it."""
        eval_id = request_holdout(self._ledger, strategy_hash, protocol_id, self.agent_name,
                                  AGENT, model_id=self.model_id,
                                  protocols_dir=self._protocols_dir)
        return {"eval_id": eval_id, "status": "pending"}
