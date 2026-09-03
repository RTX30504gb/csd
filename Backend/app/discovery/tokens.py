"""ERC-20 detector (spec sec.7).

Algorithm
---------
1. On every block the listener hands us, scan ``contract_deployments``
   for rows where ``is_erc20=false AND erc20_checked_at IS NULL``,
   ordered by id, capped at ``batch_size`` (default 20).
2. For each row, issue four read-only ``eth_call``s against the
   contract: ``name()``, ``symbol()``, ``decimals()``,
   ``totalSupply()``. Function selectors are hard-coded below.
3. If all four succeed and decode:
      - upsert into ``tokens`` (unique on ``contract_address``)
      - set ``is_erc20=true, erc20_checked_at=now()`` on the
        ``contract_deployments`` row
   If any call reverts (a non-ERC-20 contract typically reverts on
   ``name()``):
      - set ``erc20_checked_at=now()`` only. We do NOT mark the
        contract as ``is_erc20=true``; the spec is explicit: "Do
        not assume every deployed contract is a token."
   If a transport-level RPC error occurs (timeout, 5xx, rate
   limit):
      - leave both columns untouched. The deployment will be
        retried on a later block.

We deliberately do NOT use web3.py contract classes here. We only
need four selectors; hand-decoding keeps the dependency surface
small and lets the unit tests stub the provider with raw bytes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.database.models import ContractDeployment, Token

logger = logging.getLogger(__name__)

# Function selectors for the four ERC-20 standard methods. Computed
# as keccak256(signature)[:4]. Hard-coded so we don't need an
# import-time dependency on eth_utils / web3.
SELECTOR_NAME = "0x06fdde03"
SELECTOR_SYMBOL = "0x95d89b41"
SELECTOR_DECIMALS = "0x313ce567"
SELECTOR_TOTAL_SUPPLY = "0x18160ddd"

# Cap how many deployments we probe per block. Keeps a single tick
# bounded against the public RPC's rate limit. Backlog clears
# automatically over subsequent blocks.
DEFAULT_BATCH_SIZE = 20

# Cap on name/symbol length we persist. Some projects set extremely
# long strings; truncating protects row size and the eventual UI.
MAX_STRING_LEN = 256


def make_on_block(
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
):
    """Return an ``on_block(block)`` callback that probes for ERC-20s.

    The ``block`` argument is required by the listener's callback
    contract; we use it to (a) avoid running twice in the same tick
    and (b) warm the timestamp cache for tokens created in the
    block we just processed.
    """

    # Per-tick cache so a batch of deployments created in the same
    # block don't each trigger a get_block RPC.
    _ts_cache: dict[int, datetime] = {}

    async def on_block(block: dict) -> None:
        await process_token_discovery(block, provider, session_factory, batch_size, _ts_cache)

    return on_block


async def process_token_discovery(
    block: dict,
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
    ts_cache: dict[int, datetime] | None = None,
) -> None:
    # Warm the cache for the block we just processed.
    try:
        # We use a helper to handle the cache
        async def _block_timestamp(n: int) -> datetime:
            if ts_cache is not None and n in ts_cache:
                return ts_cache[n]
            b = await provider.get_block(n, full_transactions=False)
            ts = datetime.fromtimestamp(int(b["timestamp"]), tz=timezone.utc)
            if ts_cache is not None:
                ts_cache[n] = ts
            return ts

        await _block_timestamp(int(block["number"]))
    except Exception:  # noqa: BLE001
        # Cache warming is best-effort; the detector will fall
        # back to now() if it ever misses.
        pass

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ContractDeployment)
                .where(
                    ContractDeployment.is_erc20.is_(False),
                    ContractDeployment.erc20_checked_at.is_(None),
                )
                .order_by(ContractDeployment.id.asc())
                .limit(batch_size)
            )
        ).scalars().all()
        if not rows:
            return

        confirmed: list[dict] = []
        now = datetime.now(timezone.utc)

        async def _safe_block_timestamp(block_number: int) -> datetime:
            try:
                return await _block_timestamp(block_number)
            except Exception:
                return now

        for row in rows:
            # We pass the internal _block_timestamp helper for caching
            async def _internal_ts(n: int) -> datetime:
                if ts_cache is not None and n in ts_cache:
                    return ts_cache[n]
                b = await provider.get_block(n, full_transactions=False)
                ts = datetime.fromtimestamp(int(b["timestamp"]), tz=timezone.utc)
                if ts_cache is not None:
                    ts_cache[n] = ts
                return ts

            outcome, decoded = await _probe_one(provider, row.contract_address)
            if outcome is _PROBE_OK:
                logger.info("[ERC20] Token detected: %s (%s)", decoded["symbol"], row.contract_address)
                ts = await _safe_block_timestamp(row.creation_block)
                confirmed.append(
                    {
                        "contract_address": row.contract_address,
                        "deployer": row.deployer,
                        "name": decoded["name"],
                        "symbol": decoded["symbol"],
                        "decimals": decoded["decimals"],
                        "total_supply": decoded["total_supply"],
                        "creation_block": row.creation_block,
                        "creation_timestamp": ts,
                        "detected_at": now,
                    }
                )
                row.is_erc20 = True
                row.erc20_checked_at = now
            elif outcome is _PROBE_REVERT:
                row.erc20_checked_at = now

        if confirmed:
            stmt = (
                pg_insert(Token)
                .values(confirmed)
                .on_conflict_do_nothing(index_elements=["contract_address"])
            )
            await session.execute(stmt)
            logger.info("[DATABASE] Tokens saved: %d", len(confirmed))
            logger.info(
                "block %s: %d ERC-20 probe(s), %d confirmed",
                block["number"], len(rows), len(confirmed),
            )
        else:
            logger.info(
                "block %s: %d ERC-20 probe(s), 0 confirmed",
                block["number"], len(rows),
            )
        await session.commit()


# --- probe outcomes --------------------------------------------------
# Sentinel objects so the caller can branch without exception
# handling for control flow.
class _Outcome:
    pass


_PROBE_OK = _Outcome()         # all four eth_calls returned and decoded
_PROBE_REVERT = _Outcome()     # at least one call reverted: not an ERC-20
_PROBE_TRANSPORT = _Outcome()  # transport-level error: leave for retry

# Empty-decoded data returned on non-OK outcomes so the caller can
# always destructure without a None check.
_EMPTY_DECODED: dict = {
    "name": None, "symbol": None, "decimals": None, "total_supply": None,
}


async def _probe_one(
    provider: BlockchainProvider,
    address: str,
) -> tuple[_Outcome, dict]:
    """Probe a single address.

    Returns ``(outcome, decoded)`` where:
      - ``outcome is _PROBE_OK`` and ``decoded`` has name/symbol/
        decimals/total_supply (string truncated to 256 chars,
        integers intact)
      - ``outcome is _PROBE_REVERT``: contract is not ERC-20;
        ``decoded`` is the empty placeholder.
      - ``outcome is _PROBE_TRANSPORT``: network/RPC error;
        ``decoded`` is the empty placeholder. The row's columns
        are left untouched so the next tick retries.

    Does NOT raise on transport errors -- it classifies them
    and returns. The caller can thus probe a batch and have one
    transient failure not abort the entire tick.
    """
    addr = address
    # name()
    try:
        name_raw = await provider.get_eth_call(addr, SELECTOR_NAME)
        logger.info("name_raw for %s: %s", addr, name_raw.hex() if name_raw else "None")
    except Exception as e:  # noqa: BLE001
        outcome = _classify_probe_error(e)
        return outcome, dict(_EMPTY_DECODED)
    # symbol()
    try:
        symbol_raw = await provider.get_eth_call(addr, SELECTOR_SYMBOL)
        logger.info("symbol_raw for %s: %s", addr, symbol_raw.hex() if symbol_raw else "None")
    except Exception as e:  # noqa: BLE001
        outcome = _classify_probe_error(e)
        return outcome, dict(_EMPTY_DECODED)
    # decimals()
    try:
        decimals_raw = await provider.get_eth_call(addr, SELECTOR_DECIMALS)
        logger.info("decimals_raw for %s: %s", addr, decimals_raw.hex() if decimals_raw else "None")
    except Exception as e:  # noqa: BLE001
        outcome = _classify_probe_error(e)
        return outcome, dict(_EMPTY_DECODED)
    # totalSupply()
    try:
        total_supply_raw = await provider.get_eth_call(addr, SELECTOR_TOTAL_SUPPLY)
        logger.info("totalSupply_raw for %s: %s", addr, total_supply_raw.hex() if total_supply_raw else "None")
    except Exception as e:  # noqa: BLE001
        outcome = _classify_probe_error(e)
        return outcome, dict(_EMPTY_DECODED)

    try:
        name = _decode_string(name_raw)
        symbol = _decode_string(symbol_raw)
        decimals = _decode_uint8(decimals_raw)
        total_supply = _decode_uint256(total_supply_raw)
    except DecodeError:
        return _PROBE_REVERT, dict(_EMPTY_DECODED)

    # Sanity: decimals must be 0..255. If decimals is None (empty
    # response from a non-contract), the contract is definitely
    # not ERC-20.
    if decimals is None or decimals > 255:
        return _PROBE_REVERT, dict(_EMPTY_DECODED)
    if total_supply is None:
        return _PROBE_REVERT, dict(_EMPTY_DECODED)

    decoded = {
        "name": _truncate(name) if name is not None else None,
        "symbol": _truncate(symbol) if symbol is not None else None,
        "decimals": decimals,
        "total_supply": total_supply,
    }
    return _PROBE_OK, decoded


def _classify_probe_error(e: Exception) -> _Outcome:
    """Decide whether a single eth_call failure means 'not ERC-20' or
    'transient, retry later'."""
    name = type(e).__name__
    # web3.py raises ContractLogicError / BadFunctionCallOutput /
    # ABIDecodingError when the call reverted. Any of those means
    # "this contract is not ERC-20".
    revert_like = (
        "ContractLogicError",
        "BadFunctionCallOutput",
        "ABIDecodingError",
        "InvalidFunctionCall",
        "Web3ValueError",
    )
    if name in revert_like or "revert" in str(e).lower():
        return _PROBE_REVERT
    # Anything else (timeout, RPC error, aiohttp error) is transient.
    # We do NOT re-raise: the caller batches multiple probes in one
    # tick and a single transport failure must not abort the rest.
    return _PROBE_TRANSPORT


async def _safe_block_timestamp(provider, block_number, ts_fn):
    try:
        return await ts_fn(block_number)
    except Exception:  # noqa: BLE001
        # If we cannot resolve the timestamp, fall back to "now".
        # creation_block is still correct; the timestamp is just
        # for display.
        return datetime.now(timezone.utc)


# --- ABI decoders (no external dep) ----------------------------------
class DecodeError(Exception):
    pass


def _decode_string(data: bytes) -> str | None:
    """Decode an ABI-encoded ``string`` or legacy ``bytes32`` return.

    The ERC-20 spec says ``name()`` and ``symbol()`` return ``string``,
    but a non-trivial fraction of deployed tokens (most famously USDT
    on Ethereum, and many forks on Base) still implement them as
    ``bytes32`` for gas savings. We accept both layouts so we don't
    mis-classify well-known tokens as "not ERC-20".

    Layout 1 — ``string``:
        [0..32)  : offset to string data (always 0x20)
        [32..64) : length N (uint256)
        [64..)   : utf-8 bytes, padded to 32-byte boundary

    Layout 2 — ``bytes32`` (legacy):
        Exactly 32 bytes. Right-padded with ``\\x00``; we strip the
        padding before decoding as utf-8. If the result is not valid
        utf-8 we treat it as non-ERC-20.

    Returns None for the empty string (N == 0) and for an
    all-zero bytes32, since some contracts legitimately have no
    name/symbol.
    """
    if not data:
        raise DecodeError("empty response")
    # Layout 2: bytes32 — accept before the length check so a
    # 32-byte response is never mis-decoded as "length=0 string".
    if len(data) == 32:
        stripped = data.rstrip(b"\x00")
        if not stripped:
            return None
        if len(stripped) > MAX_STRING_LEN:
            raise DecodeError(f"bytes32 too large: {len(stripped)}")
        try:
            return stripped.decode("utf-8")
        except UnicodeDecodeError as e:
            raise DecodeError(f"bytes32 utf-8 decode: {e}")
    # Layout 1: string
    if len(data) < 64:
        raise DecodeError(f"response too short: {len(data)}")
    offset = int.from_bytes(data[0:32], "big")
    if offset != 32:
        raise DecodeError(f"unexpected offset: {offset}")
    length = int.from_bytes(data[32:64], "big")
    if length == 0:
        return None
    if length > MAX_STRING_LEN * 4:  # 4 bytes/char worst case
        raise DecodeError(f"length too large: {length}")
    payload = data[64 : 64 + length]
    if len(payload) < length:
        raise DecodeError("truncated payload")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DecodeError(f"utf-8 decode: {e}")


def _decode_uint8(data: bytes) -> int | None:
    if not data or len(data) < 32:
        raise DecodeError(f"uint8 response too short: {len(data)}")
    return int.from_bytes(data[31:32], "big")


def _decode_uint256(data: bytes) -> int:
    if not data or len(data) < 32:
        raise DecodeError(f"uint256 response too short: {len(data)}")
    return int.from_bytes(data[0:32], "big")


def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    if len(s) <= MAX_STRING_LEN:
        return s
    return s[:MAX_STRING_LEN]