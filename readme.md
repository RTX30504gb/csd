# Real-Time Crypto Rug-Pull Detection Engine

A real-time blockchain security system that monitors newly launched ERC-20
tokens on Base, analyzes their smart contracts, liquidity, holders, deployer
history, wallet relationships, and trading behavior, and produces an
**explainable** rug-pull risk score.

**Goal:** early-warning intelligence, not certainty. Every score ships
with the evidence that produced it.

## Status

- [x] **Phase 1** - Base RPC connection (HTTP)
- [x] **Phase 2** - Block listener (HTTP polling, DB checkpoint)  <- *current*
- [x] Phase 3 - Contract deployment detection
- [x] Phase 4 - ERC-20 detection
- [ ] Phase 5 - Token database
- [ ] Phase 6 - DEX / liquidity discovery
- [ ] ...
- [ ] Phase 20 - Real-world asset verification extension

See the full spec / dev order in the project plan.

## Stack

| Layer        | Tech                                    |
|--------------|-----------------------------------------|
| Backend      | Python + FastAPI                        |
| Blockchain   | web3.py (HTTP RPC)                      |
| Database     | PostgreSQL 16                           |
| Cache        | Redis 7                                 |
| Frontend     | React + TypeScript (later)              |
| ML (later)   | XGBoost                                 |

## Repo layout

```
rug-detector/
  docker-compose.yml          # postgres + redis
  .env.example                # copy to backend/.env
  backend/
    pyproject.toml
    app/
      main.py                 # FastAPI + lifespan
      config.py
      blockchain/
        provider.py           # BlockchainProvider (swappable)
        listener.py           # HTTP polling, DB checkpoint
      database/
        database.py
        models.py
    tests/
      test_listener.py
  frontend/                   # later
```

## Quick start (Phase 2)

```bash
# 1. infra
docker compose up -d

# 2. backend env
cd backend
cp ../.env.example .env
# edit BASE_RPC_URL if you want a paid RPC

# 3. deps
python -m venv .venv && .venv\Scripts\activate     # Windows
# source .venv/bin/activate                          # macOS / Linux
pip install -e ".[dev]"

# 4. smoke test the listener
python -m tests.test_listener

# 5. or run the API
uvicorn app.main:app --reload
```

## Listener design (Phase 2)

```
HTTP RPC -> get latest block
        -> compare with last_processed_block (Postgres)
        -> process every block in (last, latest] sequentially
        -> update checkpoint after each block
        -> resume from checkpoint on restart
```

Polling interval is `BLOCK_POLL_INTERVAL` (default 2s). The provider is
injected so a `WebSocketBlockListener` can replace `HttpRpcProvider`
later without touching the rest of the pipeline.
