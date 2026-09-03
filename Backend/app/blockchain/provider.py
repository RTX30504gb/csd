"""Blockchain provider abstraction.

Phase 2 implements only HTTP RPC polling. A WebSocket provider can be
added later (e.g. WebSocketBlockListener in app.blockchain.listener_websocket)
without changing the listener or downstream consumers.
"""
import asyncio
from abc import ABC, abstractmethod


from web3 import AsyncWeb3
from web3.providers.rpc import AsyncHTTPProvider

from app.config import get_settings

import logging

logger = logging.getLogger(__name__)

class BlockchainProvider(ABC):
    """Thin interface the listener depends on.

    We deliberately do NOT expose the full AsyncWeb3 surface here. The
    listener only needs block numbers and (later) blocks / receipts. By
    keeping this small, swapping HTTP for WebSocket or an alternative
    transport is a one-file change.
    """

    @abstractmethod
    async def get_latest_block_number(self) -> int:
        """Return the chain head block number."""
        ...

    @abstractmethod
    async def get_block(self, block_number: int, full_transactions: bool = False) -> dict:
        """Return a normalized block dict.

        When ``full_transactions`` is False (default for backwards
        compatibility), ``transactions`` is a list of tx hashes (hex
        strings, 0x-prefixed).

        When ``full_transactions`` is True, ``transactions`` is a list of
        normalized tx dicts:

            {
              "hash":  str,   # 0x-prefixed
              "from":  str,
              "to":    str | None,   # None for contract creation
              "value": int,         # wei
              "nonce": int,
              "input": str,         # 0x-prefixed calldata
            }
        """
        ...

    @abstractmethod
    async def get_transaction_receipt(self, tx_hash: str) -> dict:
        """Return a normalized transaction receipt.

        Shape::

            {
              "transactionHash": str,
              "blockNumber":      int,
              "from":             str,
              "to":               str | None,
              "contractAddress":  str | None,   # set iff contract creation
              "gasUsed":          int,
              "status":           int,         # 1 = success, 0 = revert
              "logs":             list[dict],
            }
        """
        ...

    @abstractmethod
    async def get_eth_call(self, to: str, data: str) -> bytes:
        """Issue a read-only ``eth_call`` and return the raw response.

        ``to`` is the target contract address. ``data`` is the
        0x-prefixed calldata (function selector + ABI-encoded args).
        Returns the unparsed response as raw bytes (the caller is
        responsible for ABI-decoding).

        We deliberately do NOT catch reverts at this layer: a
        non-ERC-20 contract reverts on ``name()`` with a specific
        error and the detector needs to be able to tell the
        difference between "revert" and "RPC error" so it can mark
        the contract as probed-but-not-ERC20 vs. leave it for retry.
        Re-raises ``web3.exceptions.ContractLogicError`` (or
        ``Web3RPCError`` for transport-level failures) so the
        detector can decide.
        """
        ...

    @abstractmethod
    async def get_code(self, address: str) -> bytes:
        """Return the deployed runtime bytecode at ``address``.

        Empty ``bytes()`` for an EOA or self-destructed contract --
        this is normal ``eth_getCode`` behaviour (returns ``0x``),
        not an error, so callers should not treat it as a failure.
        """
        ...

    @property
    @abstractmethod
    def chain_id(self) -> int:
        ...


class HttpRpcProvider(BlockchainProvider):
    """Async HTTP polling provider.

    Uses web3.py's AsyncWeb3 with AsyncHTTPProvider. No websockets, no
    IPC - matches Phase 2 spec ("Request the latest Base block. Compare
    it with the last processed block.").
    """

    def __init__(self, rpc_url: str | None = None, chain_id: int | None = None) -> None:
        settings = get_settings()
        self._rpc_url = rpc_url or settings.base_rpc_url
        self._chain_id = chain_id or settings.base_chain_id
        self._w3 = AsyncWeb3(AsyncHTTPProvider(self._rpc_url))
        self._call_count = 0
        logger.info("[RPC] Connected to %s (chain_id=%s)", self._rpc_url, self._chain_id)

    @property
    def call_count(self) -> int:
        """Return total number of RPC calls made by this provider."""
        return self._call_count

    @property
    def chain_id(self) -> int:
        return self._chain_id

    async def get_latest_block_number(self) -> int:
        self._call_count += 1
        # eth_blockNumber returns hex; AsyncWeb3 coerces to int.
        return await asyncio.wait_for(self._w3.eth.block_number, timeout=10.0)

    async def get_block(self, block_number: int, full_transactions: bool = False) -> dict:
        self._call_count += 1
        block = await asyncio.wait_for(
            self._w3.eth.get_block(block_number, full_transactions=full_transactions),
            timeout=10.0,
        )
        if full_transactions:

            txs = [
                {
                    "hash": tx["hash"].hex(),
                    "from": tx["from"],
                    "to": tx["to"],  # None for contract creation
                    "value": int(tx["value"]),
                    "nonce": int(tx["nonce"]),
                    "input": tx["input"],
                }
                for tx in block["transactions"]
            ]
        else:
            txs = [tx.hex() for tx in block["transactions"]]
        return {
            "number": int(block["number"]),
            "hash": block["hash"].hex(),
            "timestamp": int(block["timestamp"]),
            "transactions": txs,
        }

    async def get_transaction_receipt(self, tx_hash: str) -> dict:
        self._call_count += 1
        receipt = await asyncio.wait_for(self._w3.eth.get_transaction_receipt(tx_hash), timeout=10.0)
        return {
            "transactionHash": receipt["transactionHash"].hex(),
            "blockNumber": int(receipt["blockNumber"]),
            "from": receipt["from"],
            "to": receipt["to"],
            "contractAddress": receipt["contractAddress"],
            "gasUsed": int(receipt["gasUsed"]),
            "status": int(receipt["status"]),
            "logs": [dict(log) for log in receipt["logs"]],
        }

    async def get_eth_call(self, to: str, data: str) -> bytes:
        # web3.py v7 strictly requires EIP-55 checksum addresses for
        # eth_call. We store all addresses lower-case in the DB per
        # spec, so re-checksum before the call. Falls back to the
        # raw input on ``to_checksum_address`` failure (invalid
        # length, non-hex) so the detector can surface the
        # underlying error.
        try:
            to_checksum = self._w3.to_checksum_address(to)
        except (ValueError, TypeError):
            to_checksum = to
        self._call_count += 1
        result_hex = await asyncio.wait_for(
            self._w3.eth.call({"to": to_checksum, "data": data}), timeout=10.0
        )
        # AsyncWeb3 returns HexBytes; coerce to bytes so the detector
        # can ABI-decode without depending on web3 types.
        return bytes(result_hex)

    async def get_code(self, address: str) -> bytes:
        try:
            addr_checksum = self._w3.to_checksum_address(address)
        except (ValueError, TypeError):
            addr_checksum = address
        self._call_count += 1
        code = await asyncio.wait_for(self._w3.eth.get_code(addr_checksum), timeout=10.0)
        return bytes(code)
