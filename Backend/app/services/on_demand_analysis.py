import logging
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
import numpy as np

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.blockchain.provider import HttpRpcProvider
from app.database.models import (
    Token,
    ContractDeployment,
    ContractRiskFlags,
    LiquidityPool,
    RiskScore
)
from app.discovery.tokens import _probe_one, _PROBE_OK
from app.discovery.contract_risk import _analyze_token
from app.discovery.liquidity import _discover_pools_for_token
from app.discovery.holder_analysis import _reconstruct_balances
from app.services.risk_engine import risk_engine
from app.services.ml_inference import ml_inference
from app.services.risk_features import compute_risk_features
from app.services.mechanical_verification import mechanical_verification

logger = logging.getLogger(__name__)

# Overall timeout for the entire on-demand analysis request
OVERALL_REQUEST_TIMEOUT = 30.0
# Individual component timeouts
ERC20_TIMEOUT = 10.0
LATEST_BLOCK_TIMEOUT = 5.0
CONTRACT_RISK_TIMEOUT = 10.0
LIQUIDITY_TIMEOUT = 10.0
HOLDER_ANALYSIS_TIMEOUT = 15.0  # Only if we expect it to be quick

async def analyze_token_on_demand(
    provider: HttpRpcProvider,
    session_factory: async_sessionmaker,
    address: str,
) -> dict:
    """
    Perform a direct analysis of a single token contract address on Base.

    This endpoint provides prompt results by avoiding expensive historical
    scans and using only evidence that can be obtained quickly.
    Core analysis results are persisted for future use.
    """
    overall_start = datetime.now(timezone.utc)
    addr = address.strip().lower()

    try:
        # Enforce overall timeout
        return await asyncio.wait_for(
            _analyze_token_internal(provider, session_factory, addr, overall_start),
            timeout=OVERALL_REQUEST_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error("Overall request timeout exceeded")
        return {"error": "Analysis request timed out", "type": "TIMEOUT"}
    except Exception as e:
        logger.exception("Unexpected error during on-demand analysis: %s", e)
        return {"error": "Internal server error during analysis", "type": "INTERNAL_ERROR"}

async def _analyze_token_internal(
    provider: HttpRpcProvider,
    session_factory: async_sessionmaker,
    addr: str,
    overall_start: datetime
) -> dict:
    """Internal analysis function wrapped by overall timeout."""

    # 1. Validate address format
    if not addr.startswith("0x") or len(addr) != 42:
        return {"error": "Invalid EVM address format", "type": "INVALID_ADDRESS"}

    # 2. Bytecode check
    code = await asyncio.wait_for(
        provider.get_code(addr),
        timeout=LATEST_BLOCK_TIMEOUT
    )
    if not code:
        return {"error": "No contract bytecode found at this address", "type": "NOT_A_CONTRACT"}

    # 3. ERC-20 Detection
    try:
        outcome, decoded = await asyncio.wait_for(
            _probe_one(provider, addr),
            timeout=ERC20_TIMEOUT
        )
    except asyncio.TimeoutError:
        return {"error": "ERC-20 probe timed out", "type": "TIMEOUT"}

    if outcome is not _PROBE_OK:
        return {"error": "Contract is not a valid ERC-20 token", "type": "NOT_ERC20"}

    # 4. Block Context (only get latest block if we need it for analysis)
    latest_block = None
    try:
        latest_block = await asyncio.wait_for(
            provider.get_latest_block_number(),
            timeout=LATEST_BLOCK_TIMEOUT
        )
    except Exception as e:
        logger.warning("Failed to get latest block: %s", e)
        # Continue without latest block - some analyses may still work

    # 5. Persist or update core analysis records
    async with session_factory() as session:
        # 5a. Ensure ContractDeployment record exists
        deployment_record = (
            await session.execute(
                select(ContractDeployment).where(ContractDeployment.contract_address == addr)
            )
        ).scalars().first()

        if not deployment_record:
            # Try to get deployer from creation transaction if we can determine it quickly
            # For now, we'll leave deployer as None and let it be updated by the block listener
            creation_tx = await asyncio.wait_for(
                provider.get_transaction_receipt(deployment_record.creation_tx),
                timeout=LATEST_BLOCK_TIMEOUT
            )
            if creation_tx:
                deployment_record.deployer = creation_tx['from']
                deployment_record.created_at = datetime.fromtimestamp(creation_tx['block_timestamp'], timezone.utc)
                deployment_record.creation_block = creation_tx['block_number']
            deployment_record = ContractDeployment(
                contract_address=addr,
                deployer=None,  # Will be populated by block listener if needed
                creation_tx=None,
                creation_block=None,
                created_at=None,
                is_erc20=True,  # We know it's ERC-20 from our check
                erc20_checked_at=datetime.now(timezone.utc)
            )
            session.add(deployment_record)
            await session.flush()
        elif not deployment_record.is_erc20:
            # Update existing record to mark it as ERC-20
            deployment_record.is_erc20 = True
            deployment_record.erc20_checked_at = datetime.now(timezone.utc)
            await session.flush()

        # 5b. Ensure Token record exists
        token_record = (
            await session.execute(
                select(Token).where(Token.contract_address == addr)
            )
        ).scalars().first()

        if not token_record:
            # Create minimal token record with what we know
            token_record = Token(
                contract_address=addr,
                deployer=None,  # Will be updated when we have deployment info
                name=decoded["name"],
                symbol=decoded["symbol"],
                decimals=decoded["decimals"],
                total_supply=decoded["total_supply"],
                creation_block=None,  # Will be updated by block listener if needed
                creation_timestamp=None,
                detected_at=datetime.now(timezone.utc),
                liquidity_checked_at=None,
                contract_analyzed_at=None,
                holder_analysis_analyzed_at=None,
                wallet_graph_analyzed_at=None,
            )
            session.add(token_record)
            await session.flush()
        else:
            # Update existing token record with latest metadata
            needs_update = False
            if token_record.name != decoded["name"]:
                token_record.name = decoded["name"]
                needs_update = True
            if token_record.symbol != decoded["symbol"]:
                token_record.symbol = decoded["symbol"]
                needs_update = True
            if token_record.decimals != decoded["decimals"]:
                token_record.decimals = decoded["decimals"]
                needs_update = True
            if token_record.total_supply != decoded["total_supply"]:
                token_record.total_supply = decoded["total_supply"]
                needs_update = True
            if needs_update:
                token_record.detected_at = datetime.now(timezone.utc)
                await session.flush()

        # 5c. Persist ContractRiskFlags if we performed the analysis
        risk_flags = None
        if latest_block is not None:
            try:
                risk_flags = await asyncio.wait_for(
                    _analyze_token(provider, addr, latest_block, datetime.now(timezone.utc)),
                    timeout=CONTRACT_RISK_TIMEOUT
                )
                if risk_flags:
                    # Upsert the risk flags
                    stmt = (
                        pg_insert(ContractRiskFlags)
                        .values(
                            token_address=addr,
                            has_owner_function=risk_flags.has_owner_function,
                            owner_address=risk_flags.owner_address,
                            owner_renounced=risk_flags.owner_renounced,
                            selectors_found=risk_flags.selectors_found,
                            bytecode_size=risk_flags.bytecode_size,
                            analyzed_block=latest_block,
                            analyzed_at=datetime.now(timezone.utc),
                            has_mint=risk_flags.has_mint,
                            has_blacklist=risk_flags.has_blacklist,
                            has_pause=risk_flags.has_pause,
                            has_tax_control=risk_flags.has_tax_control,
                            has_max_tx_control=risk_flags.has_max_tx_control,
                            has_max_wallet_control=risk_flags.has_max_wallet_control,
                            has_fee_exclusion_control=risk_flags.has_fee_exclusion_control,
                            has_trading_control=risk_flags.has_trading_control,
                            is_upgradeable_proxy=risk_flags.is_upgradeable_proxy,
                        )
                        .on_conflict_do_update(
                            index_elements=["token_address"],
                            set_={
                                "has_owner_function": risk_flags.has_owner_function,
                                "owner_address": risk_flags.owner_address,
                                "owner_renounced": risk_flags.owner_renounced,
                                "selectors_found": risk_flags.selectors_found,
                                "bytecode_size": risk_flags.bytecode_size,
                                "analyzed_block": latest_block,
                                "analyzed_at": datetime.now(timezone.utc),
                                "has_mint": risk_flags.has_mint,
                                "has_blacklist": risk_flags.has_blacklist,
                                "has_pause": risk_flags.has_pause,
                                "has_tax_control": risk_flags.has_tax_control,
                                "has_max_tx_control": risk_flags.has_max_tx_control,
                                "has_max_wallet_control": risk_flags.has_max_wallet_control,
                                "has_fee_exclusion_control": risk_flags.has_fee_exclusion_control,
                                "has_trading_control": risk_flags.has_trading_control,
                                "is_upgradeable_proxy": risk_flags.is_upgradeable_proxy,
                            }
                        )
                    )
                    await session.execute(stmt)
                    await session.flush()
            except Exception as e:
                logger.warning("Contract risk analysis failed: %s", e)
                risk_flags = None

        # 5d. Persist LiquidityPool records if we found any (and analysis was successful)
        pools = []
        if latest_block is not None:
            try:
                pools_result, transport_failed = await asyncio.wait_for(
                    _discover_pools_for_token(provider, addr, {"number": latest_block}),
                    timeout=LIQUIDITY_TIMEOUT
                )
                if not transport_failed:
                    pools = pools_result
                    if pools:
                        # Upsert liquidity pools
                        for pool_dict in pools:
                            stmt = (
                                pg_insert(LiquidityPool)
                                .values(**pool_dict)
                                .on_conflict_do_update(
                                    index_elements=["pool_address"],
                                    set_={
                                        "token_address": pool_dict["token_address"],
                                        "dex": pool_dict["dex"],
                                        "pair_asset": pool_dict["pair_asset"],
                                        "fee_tier": pool_dict["fee_tier"],
                                        "reserve_token": pool_dict["reserve_token"],
                                        "reserve_pair": pool_dict["reserve_pair"],
                                        "discovered_block": pool_dict["discovered_block"],
                                        "discovered_at": pool_dict["discovered_at"],
                                        "last_synced_at": pool_dict["discovered_at"],
                                    }
                                )
                            )
                            await session.execute(stmt)
                        await session.flush()
                else:
                    logger.warning("Liquidity discovery had transport failure")
            except Exception as e:
                logger.warning("Liquidity discovery failed: %s", e)

        # Commit all our persistence work
        await session.commit()

    # 6. Holder Analysis - ONLY if we have creation_block and expect it to be quick
    holders = None
    creation_block = None
    # Get creation block from our persisted token record
    async with session_factory() as session:
        token_record = (
            await session.execute(select(Token).where(Token.contract_address == addr))
        ).scalars().first()
        if token_record:
            creation_block = token_record.creation_block

    # Only attempt holder analysis if we have creation block and it's relatively recent
    # (to avoid scanning thousands of blocks which would be too slow)
    if creation_block and latest_block is not None:
        blocks_to_scan = latest_block - creation_block
        # Only attempt holder analysis if we're scanning fewer than 5000 blocks
        # This keeps the analysis bounded and quick
        # For on-demand analysis, we'll skip holder analysis entirely to keep the endpoint fast
        # Holder analysis will be handled by the background worker
        if blocks_to_scan <= 5000:
            try:
                holders = await asyncio.wait_for(
                    _reconstruct_balances(
                        provider,
                        addr,
                        creation_block,
                        latest_block,
                        min(5000, blocks_to_scan)  # Use appropriate chunk size
                    ),
                    timeout=HOLDER_ANALYSIS_TIMEOUT
                )
            except Exception as e:
                logger.warning("Holder analysis failed or timed out: %s", e)
                holders = None
        else:
            logger.info(
                f"Skipping holder analysis - would need to scan {blocks_to_scan} blocks "
                f"(creation: {creation_block}, latest: {latest_block})"
            )
        else:
            logger.info(
                f"Skipping holder analysis - missing creation_block ({creation_block}) "
                f"or latest_block ({latest_block})"
            )

    # 7. Risk Scoring - Use the existing risk engine with our persisted data
    try:
        risk_score_obj = await asyncio.wait_for(
            risk_engine.calculate_and_store_score(
                session_factory(),
                addr
            ),
            timeout=20.0  # Reasonable timeout for risk engine
        )
        risk_score = risk_score_obj.score
        risk_level = risk_score_obj.level
        risk_reasons = risk_score_obj.reasons
    except Exception as e:
        logger.warning(f"Risk engine failed: {e}")
        # Fallback to basic score if risk engine fails
        risk_score = 0
        risk_level = "Low"
        risk_reasons = ["Risk analysis temporarily unavailable"]

    # 8. Construct Final Response
    async with session_factory() as session:
        token_record = (
            await session.execute(select(Token).where(Token.contract_address == addr))
        ).scalars().first()
        deployment_record = (
            await session.execute(select(ContractDeployment).where(ContractDeployment.contract_address == addr))
        ).scalars().first()

    res = {
        "contract_address": addr,
        "deployer": token_record.deployer if token_record else None,
        "name": decoded["name"],
        "symbol": decoded["symbol"],
        "decimals": decoded["decimals"],
        "total_supply": str(int(decoded["total_supply"])) if decoded["total_supply"] else None,
        "creation_block": creation_block,
        "creation_timestamp": (
            token_record.creation_timestamp.isoformat()
            if token_record and token_record.creation_timestamp else None
        ),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "risk": {
            "score": risk_score,
            "level": risk_level,
            "reasons": risk_reasons,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        },
        "analysis_details": {
            "pools_found": len(pools),
            "holders_reconstructed": len(holders) if holders is not None else "unavailable",
            "latest_block": latest_block,
            "analysis_duration_ms": int((datetime.now(timezone.utc) - overall_start).total_seconds() * 1000)
        },
        "deployment": {
            "creation_tx": deployment_record.creation_tx if deployment_record else None,
            "created_at": (
                deployment_record.created_at.isoformat()
                if deployment_record and deployment_record.created_at else None
            ),
            "is_erc20": True,
        } if deployment_record else None
    }

    return res