import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.database.database import AsyncSessionLocal
from app.database.models import TokenSnapshot, HolderConcentration, ContractRiskFlags, LiquidityPool
from app.discovery.deployments import process_deployment_block
from app.discovery.tokens import process_token_discovery
from app.discovery.liquidity import process_liquidity_discovery
from app.discovery.monitor import process_liquidity_monitoring
from app.discovery.contract_risk import process_contract_risk
from app.discovery.wallet_graph import process_wallet_graph
from app.discovery.holder_analysis import process_holder_analysis
from sqlalchemy import select

logger = logging.getLogger(__name__)

async def handle_deployment_task(provider: BlockchainProvider, block_data: dict) -> None:
    """Process contract deployments in a block."""
    await process_deployment_block(block_data, provider, AsyncSessionLocal)

async def handle_token_task(provider: BlockchainProvider, block_data: dict) -> None:
    """Probe for ERC-20 tokens in a block."""
    # Process discovery
    await process_token_discovery(block_data, provider, AsyncSessionLocal)

    # Trigger IMMEDIATE risk analysis for newly confirmed tokens
    # and schedule snapshots for the future.
    async with AsyncSessionLocal() as session:
        from app.database.models import Token
        from sqlalchemy import select
        # Get tokens detected very recently (e.g. last 10 seconds)
        # This is a bit crude but works for this architecture.
        res = await session.execute(
            select(Token.contract_address, Token.detected_at)
            .where(Token.detected_at >= datetime.now(timezone.utc) - timedelta(seconds=10))
        )
        newly_confirmed = res.all()
        for addr, detected_at in newly_confirmed:
            logger.info("TOKEN DETECTED: %s. Triggering immediate analysis.", addr)
            try:
                from app.services.risk_engine import risk_engine
                await risk_engine.calculate_and_store_score(session, addr)
                logger.info("ANALYSIS COMPLETE for token %s", addr)
            except Exception as e:
                logger.exception("Immediate analysis failed for %s: %s", addr, e)

            from app.services.snapshot_scheduler import SnapshotScheduler
            await SnapshotScheduler.schedule_snapshots(addr, detected_at)

async def handle_liquidity_task(provider: BlockchainProvider, block_data: dict) -> None:
    """Discover liquidity pools in a block."""
    await process_liquidity_discovery(block_data, provider, AsyncSessionLocal)

async def handle_monitor_task(provider: BlockchainProvider, block_data: dict) -> None:
    """Monitor existing liquidity pools in a block."""
    await process_liquidity_monitoring(block_data, provider, AsyncSessionLocal)

async def handle_risk_task(provider: BlockchainProvider, block_data: dict) -> None:
    """Analyze contract bytecode risk in a block."""
    await process_contract_risk(block_data, provider, AsyncSessionLocal)

async def handle_wallet_task(provider: BlockchainProvider, block_data: dict) -> None:
    """Build wallet relationship graph in a block."""
    await process_wallet_graph(block_data, provider, AsyncSessionLocal)

async def handle_holder_task(provider: BlockchainProvider, block_data: dict) -> None:
    """Reconstruct holder balances in a block."""
    await process_holder_analysis(block_data, provider, AsyncSessionLocal)

async def handle_snapshot_task(provider: BlockchainProvider, data: dict) -> None:
    """Take a historical snapshot of a token's current risk features."""
    token_address = data.get("token_address")
    interval = data.get("interval")
    scheduled_at_str = data.get("scheduled_at")

    if not token_address or not interval or not scheduled_at_str:
        logger.error("Invalid snapshot task data: %s", data)
        return

    scheduled_at = datetime.fromisoformat(scheduled_at_str)
    if datetime.now(timezone.utc) < scheduled_at:
        # Not time yet - push back to queue
        # In a real system, we'd use a delayed queue or a scheduler.
        # For now, we just push it back. We add a small sleep to prevent
        # a tight CPU-burning loop when many snapshots are queued early.
        import asyncio
        await asyncio.sleep(1)
        from app.queue import task_queue
        await task_queue.push("snapshots", "take_snapshot", data)
        return

    async with AsyncSessionLocal() as session:
        # Collect current features
        # 1. Holder concentration
        holder_res = await session.execute(
            select(HolderConcentration).where(HolderConcentration.token_address == token_address)
        )
        holder = holder_res.scalars().first()

        # 2. Contract risk
        risk_res = await session.execute(
            select(ContractRiskFlags).where(ContractRiskFlags.token_address == token_address)
        )
        risk = risk_res.scalars().first()

        # 3. Liquidity
        liq_res = await session.execute(
            select(LiquidityPool).where(LiquidityPool.token_address == token_address)
        )
        pools = liq_res.scalars().all()

        # Compute risk score using the engine
        from app.services.risk_engine import risk_engine
        risk_record = await risk_engine.calculate_and_store_score(session, token_address)

        features = {
            "largest_holder_pct": holder.largest_holder_pct if holder else None,
            "top10_pct": holder.top10_pct if holder else None,
            "risk_score": risk_record.score,
            "has_mint": risk.has_mint if risk else None,
            "has_blacklist": risk.has_blacklist if risk else None,
            "pool_count": len(pools),
            "total_liquidity": sum(int(p.reserve_token or 0) for p in pools),
        }

        snapshot = TokenSnapshot(
            token_address=token_address,
            snapshot_interval=interval,
            features_json=features,
            timestamp=datetime.now(timezone.utc),
        )
        session.add(snapshot)
        await session.commit()
        logger.info("Took %s snapshot for token %s. Risk Score: %d", interval, token_address, risk_record.score)

TASK_MAP = {
    "deployments": handle_deployment_task,
    "tokens": handle_token_task,
    "liquidity": handle_liquidity_task,
    "monitor": handle_monitor_task,
    "risk": handle_risk_task,
    "wallet": handle_wallet_task,
    "holders": handle_holder_task,
    "take_snapshot": handle_snapshot_task,
}
