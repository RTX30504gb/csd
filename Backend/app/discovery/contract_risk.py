"""Contract bytecode risk analysis (spec sec.10/11).

Scope and honesty note
-----------------------
Fully answering spec sec.10's questions -- "is there a hard limit?",
"are privileged roles controlled by one wallet or multiple parties?",
"is setTax(99%) meaningfully different from setTax() capped at 5%?"
-- requires either verified Solidity source (an Etherscan/Basescan
API call, not available from this environment) or a genuine bytecode
decompiler with data-flow analysis. Neither is implemented here.

What this detector actually does, at a scope it can back up:
  1. Fetch runtime bytecode via ``eth_getCode``.
  2. Extract every 4-byte value immediately following a ``PUSH4``
     opcode (0x63). This is the standard pattern the Solidity
     compiler emits for its function-selector dispatch table
     (``PUSH4 <selector> DUP1 PUSH4/EQ ... JUMPI``), so it reliably
     surfaces every selector the contract's public ABI exposes --
     with some false positives possible if ``PUSH4`` is used to
     push an unrelated 4-byte constant elsewhere in the code. We
     accept that over-inclusiveness; a false "this function might
     exist" is a much safer failure mode for a risk detector than a
     false "this function doesn't exist".
  3. Match extracted selectors against a curated dictionary of
     signatures associated with common rug-pull mechanisms (mint,
     blacklist, pause, tax/fee setters, tx/wallet limit setters,
     proxy upgrade hooks) and set the corresponding boolean flag.
  4. If ``owner()`` is present, call it and record whether the
     returned address is the zero address (renounced) or a live
     wallet.

Every flag here means "this capability is present in the contract",
not "this capability makes the token dangerous" -- see
``ContractRiskFlags``'s class docstring for the full list of what
this deliberately does not determine.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.database.models import ContractRiskFlags, Token

logger = logging.getLogger(__name__)

PUSH4_OPCODE = 0x63

# Selectors computed as keccak256(signature)[:4]. Grouped by the risk
# category they map onto in ContractRiskFlags. A selector appearing
# in more than one contract family (e.g. both plain `mint` and a
# vault `mint`) is a known limitation of selector-only matching --
# see module docstring.
SELECTOR_GROUPS: dict[str, list[str]] = {
    "has_mint": ["0x40c10f19", "0xa0712d68"],  # mint(address,uint256) / mint(uint256)
    "has_blacklist": [
        "0xf9f92be4",  # blacklist(address)
        "0x404e5129",  # blacklist(address,bool)
        "0x153b0d1e",  # setBlacklist(address,bool)
        "0x9cfe42da",  # addBlacklist(address)
    ],
    "has_pause": ["0x8456cb59", "0x3f4ba83a"],  # pause() / unpause()
    "has_tax_control": [
        "0xc4081a4c",  # setTaxFee(uint256)
        "0x69fe0e2d",  # setFee(uint256)
        "0x0b78f9c0",  # setFees(uint256,uint256)
        "0xdc1052e2",  # setBuyTax(uint256)
        "0x8cd09d50",  # setSellTax(uint256)
    ],
    "has_max_tx_control": [
        "0xec28438a",  # setMaxTxAmount(uint256)
        "0x1e293c10",  # setMaxTransactionAmount(uint256)
    ],
    "has_max_wallet_control": [
        "0x27a14fc2",  # setMaxWalletAmount(uint256)
        "0x5d0044ca",  # setMaxWallet(uint256)
    ],
    "has_fee_exclusion_control": [
        "0x437823ec",  # excludeFromFee(address)
        "0xea2f0b37",  # includeInFee(address)
    ],
    "has_trading_control": [
        "0xc2e5ec04",  # setTradingEnabled(bool)
        "0x8a8c523c",  # enableTrading()
    ],
    "is_upgradeable_proxy": [
        "0x3659cfe6",  # upgradeTo(address)
        "0x4f1ef286",  # upgradeToAndCall(address,bytes)
    ],
}

SELECTOR_OWNER = "0x8da5cb5b"  # owner()

ZERO_ADDRESS = "0x" + "00" * 20

DEFAULT_BATCH_SIZE = 20


def _extract_selectors(bytecode: bytes) -> set[str]:
    """Return every 4-byte value that follows a PUSH4 opcode.

    Deliberately not a full EVM disassembler: we don't track jump
    destinations or distinguish opcodes from PUSH-operand data
    elsewhere in the file, since PUSH operand lengths are
    self-describing (PUSH4 always consumes exactly the next 4
    bytes) so a naive linear scan can misalign after we skip a
    push's operand region if we don't advance past it -- which we
    do below.
    """
    selectors: set[str] = set()
    i = 0
    n = len(bytecode)
    while i < n:
        op = bytecode[i]
        if op == PUSH4_OPCODE and i + 5 <= n:
            selectors.add("0x" + bytecode[i + 1 : i + 5].hex())
            i += 5
            continue
        # Any other PUSH1..PUSH32 (0x60-0x7f): skip its operand so
        # we don't misread push-data as opcodes on the next iteration.
        if 0x60 <= op <= 0x7F:
            push_len = op - 0x5F
            i += 1 + push_len
            continue
        i += 1
    return selectors


def make_on_block(
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
):
    """Return an ``on_block(block)`` callback that runs bytecode risk analysis."""

    async def on_block(block: dict) -> None:
        await process_contract_risk(block, provider, session_factory, batch_size)

    return on_block


async def process_contract_risk(
    block: dict,
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Token)
                .where(Token.contract_analyzed_at.is_(None))
                .order_by(Token.id.asc())
                .limit(batch_size)
            )
        ).scalars().all()
        if not rows:
            return

        now = datetime.now(timezone.utc)
        block_number = int(block["number"])
        analyzed = 0
        for token in rows:
            try:
                flags = await _analyze_token(provider, token.contract_address, block_number, now)
            except Exception:  # noqa: BLE001
                # Transport error -- leave contract_analyzed_at
                # untouched so this token retries next tick.
                continue
            token.contract_analyzed_at = now
            session.add(flags)
            analyzed += 1

        if analyzed:
            logger.info(
                "block %s: contract risk analysis on %d token(s)",
                block_number, analyzed,
            )
        await session.commit()


async def _analyze_token(
    provider: BlockchainProvider,
    token_address: str,
    block_number: int,
    now: datetime,
) -> ContractRiskFlags:
    code = await provider.get_code(token_address)
    selectors = _extract_selectors(code)

    flags_kwargs = {name: False for name in SELECTOR_GROUPS}
    for flag_name, group_selectors in SELECTOR_GROUPS.items():
        if selectors.intersection(group_selectors):
            flags_kwargs[flag_name] = True

    has_owner_function = SELECTOR_OWNER in selectors
    owner_address: str | None = None
    owner_renounced: bool | None = None
    if has_owner_function:
        try:
            raw = await provider.get_eth_call(token_address, SELECTOR_OWNER)
            if len(raw) >= 32:
                addr = "0x" + raw[12:32].hex()
                owner_address = addr
                owner_renounced = addr == ZERO_ADDRESS
        except Exception:  # noqa: BLE001
            # owner() selector present in the dispatch table but the
            # call itself failed/reverted (e.g. proxy delegatecall
            # quirk) -- leave owner fields unknown rather than guess.
            pass

    return ContractRiskFlags(
        token_address=token_address,
        has_owner_function=has_owner_function,
        owner_address=owner_address,
        owner_renounced=owner_renounced,
        selectors_found=",".join(sorted(selectors)),
        bytecode_size=len(code),
        analyzed_block=block_number,
        analyzed_at=now,
        **flags_kwargs,
    )
