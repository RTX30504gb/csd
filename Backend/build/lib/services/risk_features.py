"""Contract risk features (spec sec.11).

Converts the raw ``ContractRiskFlags`` (selector-presence booleans
from Phase 10) into the exact numerical feature names spec sec.11
lists as examples. This is a pure derivation over already-stored
data -- no new RPC calls, no new detector loop, no new table. It's
recomputed on read.

Naming mapping (spec name -> source):
  owner_can_mint          <- has_mint
  owner_can_blacklist     <- has_blacklist
  owner_can_pause         <- has_pause
  owner_can_modify_tax    <- has_tax_control
  owner_can_withdraw      <- NEW: re-scans the stored selector list
                             (see WITHDRAW_SELECTORS below) rather
                             than adding a column to ContractRiskFlags,
                             since this is exactly the kind of
                             re-derivation from raw data a separate
                             "features" layer exists for.
  upgradeable             <- is_upgradeable_proxy
  hidden_privileged_functions <- see below; NOT a true "found a
                             disguised backdoor" signal, see caveat.

Honesty note on ``owner_can_X`` naming
---------------------------------------
The spec's naming implies verified access control ("owner CAN do
X"), which we cannot actually confirm from selector presence alone
(see ``ContractRiskFlags``'s own docstring). We compute it as
``has_X AND has_owner_function`` -- "this capability exists AND
there's an ownership pattern in the contract" -- which is a
reasonable proxy but is presence-based, not a verified reachability
analysis. A contract with an unrestricted (non-owner-gated) mint
function would score ``owner_can_mint=0`` here despite being *more*
dangerous (anyone can mint) -- that specific case needs the deeper
bytecode analysis this module explicitly doesn't attempt.

Honesty note on ``hidden_privileged_functions``
-------------------------------------------------
We cannot detect a genuinely obfuscated/disguised backdoor from
selector matching -- that needs source-level review or a named-
function directory lookup we don't have offline. What we compute
instead: the count of selectors present in the bytecode that are
NEITHER in the standard ERC-20/Ownable allowlist NOR in any of our
known-dangerous groups. A nonzero count means "this contract has
custom logic beyond a stock token that we don't have a name for" --
worth a human look, not a confirmed backdoor. We expose the count
(``hidden_privileged_functions``) rather than collapsing it to a
boolean so a consumer can weight "1 unrecognized selector" very
differently from "40 unrecognized selectors".
"""
from __future__ import annotations

from app.database.models import ContractRiskFlags

# Selectors we recognize as "normal, expected, not privileged" for a
# standard ERC-20 (+ common OpenZeppelin Ownable/Pausable additions
# already covered by has_pause/has_owner_function elsewhere). Anything
# NOT in this set and NOT in a known-dangerous group counts toward
# hidden_privileged_functions.
KNOWN_SAFE_SELECTORS: frozenset[str] = frozenset({
    "0x06fdde03",  # name()
    "0x95d89b41",  # symbol()
    "0x313ce567",  # decimals()
    "0x18160ddd",  # totalSupply()
    "0x70a08231",  # balanceOf(address)
    "0xa9059cbb",  # transfer(address,uint256)
    "0x23b872dd",  # transferFrom(address,address,uint256)
    "0x095ea7b3",  # approve(address,uint256)
    "0xdd62ed3e",  # allowance(address,address)
    "0x8da5cb5b",  # owner()
    "0x715018a6",  # renounceOwnership()
    "0xf2fde38b",  # transferOwnership(address)
    "0x8456cb59",  # pause()
    "0x3f4ba83a",  # unpause()
    "0x5c975abb",  # paused()
})

# withdraw()/rescue-style selectors -- not tracked as a ContractRiskFlags
# column (added here instead of a migration, see module docstring).
WITHDRAW_SELECTORS: frozenset[str] = frozenset({
    "0x3ccfd60b",  # withdraw()
    "0x51cff8d9",  # withdraw(address)
    "0xf3fef3a3",  # withdraw(address,uint256)
    "0xdb2e21bc",  # emergencyWithdraw()
    "0x5312ea8e",  # emergencyWithdraw(address)
    "0x8980f11f",  # rescueTokens(address)
    "0x15dacbea",  # sweep(address)
    "0xbc157ff0",  # sweepToken(address)
})


def compute_risk_features(flags: ContractRiskFlags) -> dict:
    """Derive spec sec.11's named numeric features from stored flags.

    Returns 0/1 ints (not bools) per the spec's own example output
    format, plus the raw ``hidden_privileged_functions`` count.
    """
    stored_selectors = {
        s for s in (flags.selectors_found or "").split(",") if s
    }
    has_withdraw = bool(stored_selectors.intersection(WITHDRAW_SELECTORS))

    # Selectors already explained by a known-dangerous group (mint,
    # blacklist, etc.) aren't "hidden" -- they're identified, just
    # dangerous. Only count ones we can't attribute to ANY category.
    from app.discovery.contract_risk import SELECTOR_GROUPS
    all_known_dangerous = {s for group in SELECTOR_GROUPS.values() for s in group}
    unrecognized = stored_selectors - KNOWN_SAFE_SELECTORS - all_known_dangerous - WITHDRAW_SELECTORS

    return {
        "owner_can_mint": int(flags.has_mint and flags.has_owner_function),
        "owner_can_blacklist": int(flags.has_blacklist and flags.has_owner_function),
        "owner_can_pause": int(flags.has_pause and flags.has_owner_function),
        "owner_can_modify_tax": int(flags.has_tax_control and flags.has_owner_function),
        "owner_can_withdraw": int(has_withdraw and flags.has_owner_function),
        "upgradeable": int(flags.is_upgradeable_proxy),
        "hidden_privileged_functions": len(unrecognized),
        "owner_renounced": (
            int(flags.owner_renounced) if flags.owner_renounced is not None else None
        ),
    }


async def compute_full_risk_features(session: AsyncSession, token_address: str) -> dict:
    """Aggregate all numerical features for a token across different sources.

    This is the primary feature vector used by the ML model (Phase 18).
    """
    from sqlalchemy import select
    from app.database.models import ContractRiskFlags, HolderConcentration, LiquidityPool, Token, Wallet

    # 1. Contract Bytecode Features
    risk_res = await session.execute(
        select(ContractRiskFlags).where(ContractRiskFlags.token_address == token_address)
    )
    flags = risk_res.scalars().first()
    bytecode_features = compute_risk_features(flags) if flags else {}

    # 2. Holder Concentration Features
    holder_res = await session.execute(
        select(HolderConcentration).where(HolderConcentration.token_address == token_address)
    )
    holder = holder_res.scalars().first()
    holder_features = {
        "largest_holder_pct": float(holder.largest_holder_pct) if holder else 0.0,
        "top10_pct": float(holder.top10_pct) if holder else 0.0,
        "creator_holdings_pct": float(holder.creator_holdings_pct) if holder else 0.0,
        "holder_count": int(holder.holder_count) if holder else 0,
    }

    # 3. Liquidity Features
    liq_res = await session.execute(
        select(LiquidityPool).where(LiquidityPool.token_address == token_address)
    )
    pools = liq_res.scalars().all()
    liquidity_features = {
        "pool_count": len(pools),
        "total_liquidity": float(sum(int(p.reserve_token or 0) for p in pools)),
    }

    # 4. Deployer History Features
    token_res = await session.execute(
        select(Token).where(Token.contract_address == token_address)
    )
    token = token_res.scalars().first()
    deployer_features = {}
    if token:
        wallet_res = await session.execute(
            select(Wallet).where(Wallet.address == token.deployer)
        )
        wallet = wallet_res.scalars().first()
        if wallet:
            deployer_features = {
                "deployer_tokens_deployed": int(wallet.tokens_deployed),
                "deployer_tokens_as_transfer": int(wallet.tokens_as_transfer),
            }

    # Combine all features into a single flat vector
    return {
        **bytecode_features,
        **holder_features,
        **liquidity_features,
        **deployer_features,
    }
