from __future__ import annotations

from typing import TYPE_CHECKING

from ..commands import RefreshContracts, AcceptContract, AbandonContract, CommandResult

if TYPE_CHECKING:
    from ..game import Game


class ContractsSystem:
    def __init__(self, game: "Game"):
        self.game = game

    def handle(self, cmd):
        g = self.game

        if isinstance(cmd, RefreshContracts):
            before = len(g.contract_offers or [])
            g._refresh_contracts_if_needed()
            after = len(g.contract_offers or [])
            if after != before:
                g.emit("contracts_refreshed", before=before, after=after)
            return CommandResult(status="ok")

        if isinstance(cmd, AcceptContract):
            return g._cmd_accept_contract(cmd.cid)

        if isinstance(cmd, AbandonContract):
            return g._cmd_abandon_contract(cmd.cid)

        return None
