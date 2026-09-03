import asyncio
import logging
from sqlalchemy import select, delete
from app.database.database import AsyncSessionLocal
from app.database.models import Token, ContractDeployment, RiskScore, LiquidityPool, HolderConcentration, Wallet, WalletRelationship

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cleanup_mocks")

async def cleanup():
    async with AsyncSessionLocal() as session:
        # Addresses from seed.py
        mock_token_addresses = [
            "0x" + "1"*40,
            "0x" + "2"*40,
            "0x" + "3"*40,
        ]

        logger.info("Cleaning up mock tokens...")

        # Delete in order to respect FKs
        # WalletRelationship -> Token / Wallet
        await session.execute(
            delete(WalletRelationship).where(
                (WalletRelationship.a.in_(mock_token_addresses)) |
                (WalletRelationship.b.in_(mock_token_addresses))
            )
        )

        # Token holders, RiskScores, LiquidityPools, etc.
        await session.execute(delete(RiskScore).where(RiskScore.token_address.in_(mock_token_addresses)))
        # HolderConcentration, TokenHolder (if exists)
        # etc.

        # Use a more general approach: delete from Token and let CASCADE handle the rest
        # since the models have ondelete="CASCADE"
        await session.execute(
            delete(Token).where(Token.contract_address.in_(mock_token_addresses))
        )
        await session.execute(
            delete(ContractDeployment).where(ContractDeployment.contract_address.in_(mock_token_addresses))
        )

        # Also cleanup the wallets used in seed.py
        mock_wallet_addresses = [
            "0x" + "a"*40,
            "0x" + "b"*40,
            "0x" + "c"*40,
        ]
        await session.execute(delete(Wallet).where(Wallet.address.in_(mock_wallet_addresses)))

        await session.commit()
        logger.info("Mock data cleanup complete.")

if __name__ == "__main__":
    asyncio.run(cleanup())
