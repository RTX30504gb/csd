"""Contract-deployment detector (spec sec.6).

Algorithm
---------
1. For each block the listener hands us, fetch the full block (with
   full transaction objects) once via the provider.
2. Filter to transactions where ``to is None`` � these are the only
   ones that *can* be contract creations.
3. For each candidate, fetch the receipt. The receipt's
   ``contractAddress`` is the definitive proof of deployment; the
   receipt is the cheapest place to read it from. (A failed
   deployment leaves the receipt with ``status == 0`` and
   ``contractAddress is None``; we drop those.)
4. Upsert into ``contract_deployments`` keyed on ``contract_address``
   so re-processing a block (e.g. after a listener restart) is a
   no-op.

We deliberately do NOT use a worker queue for Phase 3. A Base block
contains a handful of deployments at most, and the work is bounded
by the listener's block-pace (one block every ~2s). Adding a queue
now would be premature; Phase 24 (async analysis) is the right
place to introduce one.
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.database.models import ContractDeployment

logger = logging.getLogger(__name__)


def _is_deployment_tx(tx: dict) -> bool:
    """A contract-creation tx has no destination address.

    ``tx["to"]`` is None for contract creation and a 0x-address for
    every other tx type. We only check ``None`` because web3.py
    normalises the zero address to the literal null only for
    contract creation, not for value-0 sends to 0x0.
    """
    return tx.get("to") is None


def _candidate_hashes(block: dict) -> Iterable[str]:
    """Yield tx hashes for likely contract creations.

    ``block["transactions"]`` may be either a list of hashes (when
    the listener requested full_transactions=False) or a list of tx
    dicts (full_transactions=True). We accept both so the detector
    can be plugged in incrementally.
    """
    for tx in block.get("transactions", []):
        if isinstance(tx, str):
            # We would need a full_block fetch here. Phase 3 wires
            # the listener to request full_transactions=True, so
            # this branch is a defensive fallback.
            yield tx
        else:
            if _is_deployment_tx(tx):
                yield tx["hash"]


def make_on_block(
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
):
    """Return an ``on_block(block)`` callback that records deployments.

    Usage in ``main.py``::

        detector = make_on_block(provider, AsyncSessionLocal)
        listener.register_on_block(detector)
    """

    async def on_block(block: dict) -> None:
        # If the block came in as just tx hashes, refetch with full
        # tx objects so we can filter cheaply.
        txs = block.get("transactions", [])
        if txs and isinstance(txs[0], str):
            block = await provider.get_block(block["number"], full_transactions=True)
            txs = block["transactions"]

        deployment_hashes = [h for h in _candidate_hashes(block)]
        if not deployment_hashes:
            return

        new_rows: list[dict] = []
        for h in deployment_hashes:
            receipt = await provider.get_transaction_receipt(h)
            if receipt.get("status") != 1:
                continue  # reverted deployment
            contract_address = receipt.get("contractAddress")
            if not contract_address:
                continue
            new_rows.append(
                {
                    "contract_address": _lower(contract_address),
                    "deployer": _lower(receipt["from"]),
                    "creation_tx": _lower(receipt["transactionHash"]),
                    "creation_block": int(receipt["blockNumber"]),
                }
            )

        if not new_rows:
            return

        async with session_factory() as session:
            inserted = await _upsert_deployments(session, new_rows)
            await session.commit()

        logger.info(
            "block %s: %d deployment candidate(s), %d new",
            block["number"],
            len(deployment_hashes),
            inserted,
        )

    return on_block


# --- helpers ---------------------------------------------------------
def _lower(addr: str) -> str:
    """Lower-case an EVM address without the 0x checksum ambiguity.

    We store addresses lower-case throughout. Display layer can
    re-checksum if needed.
    """
    if not addr:
        return addr
    return addr.lower() if addr.startswith("0x") else "0x" + addr.lower()


async def _upsert_deployments(
    session: AsyncSession, rows: list[dict]
) -> int:
    """Insert ``rows`` if not already present; return count inserted.

    Uses PostgreSQL's ``ON CONFLICT DO NOTHING`` so re-processing
    a block (after a listener restart, or because the discovery
    callback re-ran) is idempotent.
    """
    if not rows:
        return 0
    stmt = (
        pg_insert(ContractDeployment)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["contract_address"])
    )
    result = await session.execute(stmt)
    return result.rowcount or 0
