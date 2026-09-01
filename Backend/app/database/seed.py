import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
from app.database.models import (
    ContractDeployment,
    ContractRiskFlags,
    HolderConcentration,
    LiquidityEvent,
    LiquidityPool,
    ProcessedBlock,
    RiskEvent,
    RiskScore,
    Token,
    TokenHolder,
    TokenSnapshot,
    Wallet,
    WalletRelationship,
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_seed")

async def seed_database():
    async with AsyncSessionLocal() as session:
        # 1. Initialize Checkpoint
        logger.info("Initializing processed_block checkpoint...")
        checkpoint = await session.get(ProcessedBlock, 1)
        if not checkpoint:
            session.add(ProcessedBlock(id=1, block_number=0, block_hash="0x0"))
        else:
            checkpoint.block_number = 0

        # 2. Define Personas
        # format: (symbol, name, risk_level, score, risk_flags, holders_dist, liquidity_status)
        # liquidity_status: "stable", "fluctuating", "drained"
        personas = [
            {
                "symbol": "SAFE",
                "name": "Secure Token",
                "risk_level": "Low",
                "score": 10,
                "flags": {
                    "has_mint": False, "has_blacklist": False, "has_pause": False,
                    "has_tax_control": False, "has_max_tx_control": False,
                    "has_max_wallet_control": False, "has_fee_exclusion_control": False,
                    "has_trading_control": False, "is_upgradeable_proxy": False,
                    "has_owner_function": True, "owner_renounced": True,
                },
                "concentration": {"largest": 0.02, "top5": 0.10, "top10": 0.15, "top20": 0.25, "creator": 0.01},
                "liq_status": "stable",
                "liq_reserves": (1000000, 1000000), # (token, pair)
            },
            {
                "symbol": "SUS",
                "name": "Suspicious Token",
                "risk_level": "Suspicious",
                "score": 60,
                "flags": {
                    "has_mint": True, "has_blacklist": False, "has_pause": True,
                    "has_tax_control": True, "has_max_tx_control": False,
                    "has_max_wallet_control": False, "has_fee_exclusion_control": False,
                    "has_trading_control": False, "is_upgradeable_proxy": True,
                    "has_owner_function": True, "owner_renounced": False,
                },
                "concentration": {"largest": 0.15, "top5": 0.30, "top10": 0.40, "top20": 0.50, "creator": 0.10},
                "liq_status": "fluctuating",
                "liq_reserves": (500000, 500000),
            },
            {
                "symbol": "RUG",
                "name": "Rugpull Coin",
                "risk_level": "Critical",
                "score": 95,
                "flags": {
                    "has_mint": True, "has_blacklist": True, "has_pause": True,
                    "has_tax_control": True, "has_max_tx_control": True,
                    "has_max_wallet_control": True, "has_fee_exclusion_control": True,
                    "has_trading_control": True, "is_upgradeable_proxy": False,
                    "has_owner_function": True, "owner_renounced": False,
                },
                "concentration": {"largest": 0.90, "top5": 0.95, "top10": 0.97, "top20": 0.99, "creator": 0.90},
                "liq_status": "drained",
                "liq_reserves": (100, 100),
            },
        ]

        # 3. Insert Data
        for i, p in enumerate(personas):
            symbol = p["symbol"]
            logger.info(f"Seeding persona: {symbol}...")

            # Randomish addresses for the persona
            deployer_addr = f"0x{'a'*40}" if i == 0 else f"0x{'b'*40}" if i == 1 else f"0x{'c'*40}"
            token_addr = f"0x{'1'*40}" if i == 0 else f"0x{'2'*40}" if i == 1 else f"0x{'3'*40}"
            pool_addr = f"0x{'4'*40}" if i == 0 else f"0x{'5'*40}" if i == 1 else f"0x{'6'*40}"

            # A. Contract Deployment
            deployment = ContractDeployment(
                contract_address=token_addr,
                deployer=deployer_addr,
                creation_tx=f"0x{'%064x' % (1000 + i)}",
                creation_block=1000 + i,
                created_at=datetime.now(timezone.utc),
                is_erc20=True,
                erc20_checked_at=datetime.now(timezone.utc)
            )
            session.add(deployment)
            await session.flush()
            # B. Token
            token = Token(
                contract_address=token_addr,
                deployer=deployer_addr,
                name=p["name"],
                symbol=symbol,
                decimals=18,
                total_supply=int(1e9 * 10**18),
                creation_block=1000 + i,
                creation_timestamp=datetime.now(timezone.utc),
                detected_at=datetime.now(timezone.utc),
                liquidity_checked_at=datetime.now(timezone.utc),
                contract_analyzed_at=datetime.now(timezone.utc),
                holder_analysis_analyzed_at=datetime.now(timezone.utc),
                wallet_graph_analyzed_at=datetime.now(timezone.utc),
            )
            session.add(token)
            await session.flush()
            # C. Wallet
            wallet = Wallet(
                address=deployer_addr,
                tokens_deployed=1,
                tokens_as_pool=0,
                tokens_as_transfer=0,
                first_seen_block=1000 + i,
                last_seen_block=1000 + i,
                first_seen_at=datetime.now(timezone.utc),
                last_seen_at=datetime.now(timezone.utc),
            )
            session.add(wallet)
            await session.flush()
            # D. Liquidity Pool
            pool = LiquidityPool(
                token_address=token_addr,
                pool_address=pool_addr,
                dex="uniswap_v2",
               pair_asset="0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                fee_tier=None,
                is_token0=True,
                reserve_token=p["liq_reserves"][0],
                reserve_pair=p["liq_reserves"][1],
                discovered_block=1001 + i,
                discovered_at=datetime.now(timezone.utc),
                last_synced_at=datetime.now(timezone.utc),
            )
            session.add(pool)
            await session.flush()
            # E. Risk Flags
            flags = ContractRiskFlags(
                token_address=token_addr,
                **p["flags"],
                selectors_found="0xa0712d68,0x12345678",
                bytecode_size=15000,
                analyzed_block=1002 + i,
                analyzed_at=datetime.now(timezone.utc),
            )
            session.add(flags)
            await session.flush()
            # F. Holder Concentration
            conc = HolderConcentration(
                token_address=token_addr,
                largest_holder_pct=p["concentration"]["largest"],
                top5_pct=p["concentration"]["top5"],
                top10_pct=p["concentration"]["top10"],
                top20_pct=p["concentration"]["top20"],
                creator_holdings_pct=p["concentration"]["creator"],
                creator_associated_holdings_pct=p["concentration"]["creator"],
                largest_holder_address=deployer_addr,
                largest_holder_category="deployer",
                holder_count=100 if i==0 else 10 if i==2 else 50,
                analyzed_block=1003 + i,
                analyzed_at=datetime.now(timezone.utc),
            )
            session.add(conc)
            await session.flush()
            # G. Risk Score
            score = RiskScore(
                token_address=token_addr,
                score=p["score"],
                level=p["risk_level"],
                category_scores={"ml_score": p["score"]-2, "mechanical_score": p["score"]+2},
                reasons=["Mock reason for " + symbol],
                computed_at=datetime.now(timezone.utc),
                outcome="unknown"
            )
            session.add(score)
            await session.flush()
            # H. Wallet Relationship (Deployer -> Token)
            rel = WalletRelationship(
                a=deployer_addr,
                b=token_addr,
                kind="funds_token",
                weight=1,
                first_seen_block=1000 + i,
                last_seen_block=1000 + i,
                evidence_json={"tx": "0x..."},
                created_at=datetime.now(timezone.utc),
            )
            session.add(rel)
            await session.flush()   
            # I. Liquidity Events (Only for RUG)
            if p["liq_status"] == "drained":
                event = LiquidityEvent(
                    pool_address=pool_addr,
                    event_type="withdrawal",
                    metric="reserve_token",
                    value_before=1000000,
                    value_after=100,
                    percent_change=-99.99,
                    block_number=1005 + i,
                    detected_at=datetime.now(timezone.utc),
                )
                session.add(event)
                await session.flush()
        await session.commit()
        logger.info("Database successfully seeded with test personas!")

if __name__ == "__main__":
    asyncio.run(seed_database())
