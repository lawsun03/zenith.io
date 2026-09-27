"""The research agents' only interface (spec §11). Step 3 slice: protocols.

Agents read protocols and never write them. That is enforced here, not by
convention: write calls raise PermissionError and are logged with the
agent's name and model. The rest of §11 (register_family, run_dev, …)
lands in build step 7.
"""
from __future__ import annotations

import logging

from app.research.ledger import Ledger
from app.research.protocol import PROTOCOLS_DIR, verify_protocol

log = logging.getLogger(__name__)


class AgentAPI:
    def __init__(self, agent_name: str, model_id: str, ledger: Ledger,
                 protocols_dir=PROTOCOLS_DIR) -> None:
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
