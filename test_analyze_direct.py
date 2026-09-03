import asyncio
import logging
from app.blockchain.provider import HttpRpcProvider
from app.database.database import AsyncSessionLocal
from app.discovery.tokens import _probe_one, _PROBE_OK
from app.services.risk_engine import risk_engine
from app.discovery.contract_risk import process_contract_risk
from app.discovery.holder_analysis import process_holder_analysis
from app.discovery.liquidity import process_liquidity_discovery
from app.database.models import Token, ContractDeployment
from sqlalchemy import select
from datetime import datetime, timezone

async def main():
    logging.basicConfig(level=logging.INFO)
    addr = "0x4200000000000000000000000000000000000006".lower()
    provider = HttpRpcProvider()
    
    print(f"Analyzing {addr}...")
    
    # 1. Bytecode
    code = await provider.get_code(addr)
    print(f"Bytecode found: {bool(code)}")
    
    # 2. ERC-20 Probe
    async def dummy_ts(n): return datetime.now(timezone.utc)
    outcome, decoded = await _probe_one(provider, addr)
    print(f"ERC-20 Probe Outcome: {outcome == _PROBE_OK}")
    
    if outcome != _PROBE_OK:
        return

    async with AsyncSessionLocal() as session:
        # 3. Persist
        token = (await session.execute(select(Token).where(Token.contract_address == addr))).scalars().first()
        if not token:
            now = datetime.now(timezone.utc)
            deployment = ContractDeployment(
                contract_address=addr,
                deployer="0x0000000000000000000000000000000000000000",
                creation_tx="0x" + "0"*64,
                creation_block=0,
                created_at=now,
                is_erc20=True,
                erc20_checked_at=now
            )
            session.add(deployment)
            await session.flush()
            token = Token(
                contract_address=addr,
                deployer="0x0000000000000000000000000000000000000000",
                name=decoded["name"],
                symbol=decoded["symbol"],
                decimals=decoded["decimals"],
                total_supply=decoded["total_supply"],
                creation_block=0,
                creation_timestamp=now,
                detected_at=now,
                liquidity_checked_at=now,
                contract_analyzed_at=now,
                holder_analysis_analyzed_at=now,
                wallet_graph_analyzed_at=now,
            )
            session.add(token)
            await session.flush()
        
        # 4. Analysis
        dummy_block = {"number": 0, "timestamp": 0}
        print("Running contract risk...")
        await process_contract_risk(dummy_block, provider, AsyncSessionLocal)
        print("Running holder analysis...")
        await process_holder_analysis(dummy_block, provider, AsyncSessionLocal)
        print("Running liquidity discovery...")
        await process_liquidity_discovery(dummy_block, provider, AsyncSessionLocal)
        
        print("Calculating risk score...")
        risk_record = await risk_engine.calculate_and_store_score(session, addr)
        await session.commit()
        
        print(f"Analysis Complete. Score: {risk_record.score}, Level: {risk_record.level}")

if __name__ == "__main__":
    asyncio.run(main())
