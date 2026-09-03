import asyncio
import httpx
import time

async def main():
    url = "http://localhost:8000/tokens/analyze"
    payload = {"contract_address": "0x4200000000000000000000000000000000000006"}
    print(f"Analyzing {payload['contract_address']}...")
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(url, json=payload)
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
