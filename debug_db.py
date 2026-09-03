import asyncio
from sqlalchemy import select
from app.database.database import AsyncSessionLocal
from app.database.models import Token, ProcessedBlock

async def main():
    async with AsyncSessionLocal() as session:
        # Check processed block
        checkpoint = await session.get(ProcessedBlock, 1)
        if checkpoint:
            print(f"Last processed block: {checkpoint.block_number}")
        else:
            print("No processed block checkpoint found.")

        # Count tokens
        result = await session.execute(select(Token))
        tokens = result.scalars().all()
        print(f"Number of tokens in DB: {len(tokens)}")
        for t in tokens[:10]:  # Show first 10
            print(f"  - {t.contract_address} {t.symbol} {t.name} (block {t.creation_block})")

        # If there are more than 10, just say so
        if len(tokens) > 10:
            print(f"  ... and {len(tokens) - 10} more")

if __name__ == "__main__":
    asyncio.run(main())