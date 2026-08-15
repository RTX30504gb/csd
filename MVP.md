# 🚀 Rug-Pull Detector — MVP Plan

> One-shot reference for shipping the minimum viable product.
> Source of truth: `SPEC.md` §35. This document turns that into a buildable plan.
>
> **Status:** Draft v1
> **Target:** Real-time ERC-20 risk on Base, **<30s** typical latency
> **Stack:** Python 3.11 · FastAPI · web3.py · PostgreSQL · Redis · React + TypeScript

---

## 🎯 TL;DR

A working pipeline that watches Base for new ERC-20 token contracts, gathers on-chain
evidence (deployer, holders, liquidity, contract permissions, wallet relationships),
and emits an **explainable** risk score visible on a web dashboard — all in under
**30 seconds**. No ML yet. Rule-based scoring only. Single chain (Base).

---

## ✅ What "done" looks like

The MVP is shipped when the system can do **all 13 things** from `SPEC.md` §35:

- [ ] Monitor Base
- [ ] Detect newly deployed ERC-20 tokens
- [ ] Identify the deployer
- [ ] Find a relevant liquidity pool
- [ ] Analyze basic contract permissions
- [ ] Calculate holder concentration
- [ ] Analyze deployer history
- [ ] Detect basic wallet relationships
- [ ] Detect liquidity changes
- [ ] Produce an explainable risk score
- [ ] Update the score when new suspicious events occur
- [ ] Display results through a web dashboard
- [ ] Achieve typical analysis latency below 30 seconds

---

## 🚫 Out of scope (later phases)

Explicitly **not** in the MVP. If a feature is on this list, it does not block launch.

| Deferred                                  | Comes in phase |
| ----------------------------------------- | -------------- |
| ML scoring (XGBoost)                      | 18             |
| Historical backtesting harness            | 17             |
| Model evaluation & metrics dashboard      | 19             |
| Real-world asset / mechanical verification | 20           |
| Multi-chain support                       | post-MVP       |
| Sophisticated wallet-graph ML             | post-MVP       |
| Public API + auth / rate limiting         | post-MVP       |
| Mobile app                                | post-MVP       |

---

## 🏗 Architecture

```
                  ┌────────────────────┐
                  │   Base Blockchain  │
                  └─────────┬──────────┘
                            │  HTTP RPC (web3.py, async)
                            ▼
                  ┌────────────────────┐
                  │   Block Listener   │  ◀── checkpoint table
                  └─────────┬──────────┘
                            │  on_block(block)
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  ┌────────────┐    ┌──────────────┐    ┌──────────────┐
  │ Discovery  │    │  Contract    │    │  Liquidity   │
  │ deploy /   │    │  Analyzer    │    │  Monitor     │
  │ ERC-20     │    │              │    │              │
  └─────┬──────┘    └──────┬───────┘    └──────┬───────┘
        │                  │                   │
        └────────┬─────────┴───────────────────┘
                 ▼
          ┌──────────────────┐
          │   Risk Engine    │  ◀── rule-based, 5 categories
          │  rules + recs    │
          └─────┬────────────┘
                │
       ┌────────┴────────┐
       ▼                 ▼
   PostgreSQL         FastAPI
   (persistent)      (read API)
                          │
                          ▼
                  ┌──────────────────┐
                  │ React Dashboard  │
                  │ /token /wallet   │
                  │ /live            │
                  └──────────────────┘
```

**State split:**

- **PostgreSQL** = source of truth — tokens, holders, events, scores, evidence
- **Redis** = hot state — active monitoring, latest score per token, worker queues

---

## 🧰 Tech stack

| Layer         | Tool                          | Why                                  |
| ------------- | ----------------------------- | ------------------------------------ |
| RPC           | web3.py (async HTTP)          | Best-in-class Python lib             |
| API           | FastAPI                       | Async-native, fast, typed            |
| DB            | PostgreSQL 16                 | JSON columns, async drivers          |
| Cache / queue | Redis 7                       | Pub-sub, hot state                   |
| ORM           | SQLAlchemy 2 (async)          | Type-safe; migrations via Alembic    |
| Migrations    | Alembic                       | Standard for SQLAlchemy              |
| Frontend      | React + TypeScript + Vite     | Fast dev, type safety                |
| Charts        | Recharts                      | Easy to wire to risk timelines       |

> No ML libraries in the MVP image. No GPU. No Kubernetes. Plain
> `docker compose up` brings the whole stack up locally.

---

## 🔁 Data flow

1. **Block arrives** → listener picks it up.
2. **Contract discovery** → identify txs with `to == null` → grab receipt → if
   `contractAddress` is set, register.
3. **ERC-20 detection** → probe `name / symbol / decimals / totalSupply`. If they
   respond, mark as token.
4. **Liquidity discovery** → watch for pair creation events on the top DEXes
   (Uniswap V3, Aerodrome, etc.); record pool.
5. **Analyses** (run in parallel where possible):
   - Contract permissions — owner-only functions, mint, blacklist, pause, tax, upgrade
   - Holder concentration — reconstruct from Transfer events
   - Deployer history — other contracts, prior rugs, funding source
   - Wallet relationships — shared funding, common counter-parties
6. **Risk engine** → 5 category scores → weighted overall → reasons list.
7. **Persist** → token, contracts, holders, events, score, evidence.
8. **Push** → FastAPI / dashboard / live feed update.

---

## 📊 Risk score (rule-based, MVP)

Five categories, each **0–100** (higher = riskier):

| Category      | What it measures                                  | MVP signal examples |
| ------------- | ------------------------------------------------- | ------------------- |
| **Contract**  | Privileged functions, ownership, upgradeability    | can mint, can pause, can set tax, upgradeable proxy, owner not renounced |
| **Liquidity** | Pool size, lock status, LP concentration          | liquidity not locked, single-wallet LP, tiny TVL |
| **Holder**    | Concentration, infra classification               | top-10 > 70%, deployer holds > 10% |
| **Deployer**  | History, age, prior rugs, funding source           | launched 5+ tokens, prior liquidity pulls, fresh EOA funded from mixer |
| **Behavior**  | Selling patterns, mint-then-sell, blacklist usage   | creator sold, coordinated dump, mint + immediate sell |

**Aggregation (v1, hand-tuned):**
```
overall = 0.25·contract + 0.20·liquidity + 0.20·holder
        + 0.20·deployer + 0.15·behavior
```

**Levels** (from `SPEC.md` §20):

| Range   | Level       |
| ------- | ----------- |
| 0–29    | Low         |
| 30–54   | Suspicious  |
| 55–79   | High        |
| 80–100  | Critical    |

> Weights are placeholders. Every `risk_scores` row also stores the
> **outcome** (later: rug / didn't rug) so weights can be optimized against
> reality once we have data.

**Every score ships with reasons.** Never just `87`. Always:
```
87 / 100 — CRITICAL

Reasons:
1. Liquidity is not locked.
2. Deployer controls 17.8% of supply.
3. Deployer is associated with four previous suspicious launches.
4. Owner can modify transaction restrictions.
5. Six wallets share funding sources with the deployer.
```

---

## 🗄 Database (MVP tables)

| Table                    | Holds                                                                                  |
| ------------------------ | -------------------------------------------------------------------------------------- |
| `tokens`                 | address, name, symbol, decimals, total_supply, deployer, created_block, created_at    |
| `contracts`              | bytecode flags, owner, upgradeable, raw features JSON                                 |
| `deployers`              | address, first_seen_block, prior_token_count, prior_rug_count                          |
| `wallets`                | address, label (EOA / contract / pool / router / burn / …), balance snapshots          |
| `holders`                | token_address, holder_address, balance, percent, as_of_block                           |
| `pools`                  | address, token_address, pair_asset, reserves, lp_token, locked, lock_expiry            |
| `liquidity_events`       | pool_address, kind (add/remove), amount, by, block, tx                                 |
| `transfers`              | token_address, from, to, amount, block, tx                                             |
| `wallet_relationships`   | a, b, kind (funded_by / co-deployed / co-bought), weight                               |
| `risk_scores`            | token_address, score, level, category_scores JSON, reasons JSON, computed_at, outcome  |
| `risk_events`            | token_address, kind, payload JSON, block, tx                                           |
| `processed_block`        | singleton checkpoint (already exists from Phase 2)                                     |

> Every `risk_scores` row also stores the **evidence** that produced the score
> (not just the number), so we can answer *"why 91?"* after the fact.

---

## 🌐 API surface (FastAPI)

```
GET  /health
GET  /chain-info
GET  /tokens
GET  /tokens/{address}
GET  /tokens/{address}/risk
GET  /tokens/{address}/holders
GET  /tokens/{address}/liquidity
GET  /tokens/{address}/deployer
GET  /wallets/{address}
GET  /wallets/{address}/history
GET  /wallets/{address}/relationships
```

Example response (matches `SPEC.md` §25):
```json
{
  "address": "0x...",
  "risk_score": 87,
  "risk_level": "critical",
  "reasons": [
    "Liquidity is not locked",
    "High holder concentration",
    "Deployer has previous suspicious launches"
  ]
}
```

---

## 🖥 Frontend (3 pages)

1. **Token page** — risk score, category breakdown, reasons, holder pie, liquidity
   chart, event timeline.
2. **Wallet page** — reputation, tokens deployed, prior launches, related wallets,
   small relationship graph.
3. **Live feed** — running stream:
   ```
   11:42:03  → New token detected
   11:42:07  → ERC-20 confirmed
   11:42:13  → Liquidity found
   11:42:18  → Deployer analyzed
   11:42:21  → Risk = 73
   11:42:26  → Wallet cluster detected
   11:42:27  → Risk = 84
   ```

Polling interval: 2–3s. WebSocket is a nice-to-have, not required for MVP.

---

## ⏱ Latency budget

Per `SPEC.md` §27 — target **<30s** typical, hard cap **<60s**.

| Stage                   | Budget |
| ----------------------- | -----: |
| Block detected          |   2s   |
| Tx parsed               |   5s   |
| Features updated        |  10s   |
| Risk calculation        |   2s   |
| API / dashboard update  |   5s   |
| **Total**               | **24s** |

Biggest wins: parallelize contract / holder / liquidity / deployer workers; cache
provider responses; only re-fetch what changed.

---

## 🛠 Build order (MVP-relevant phases)

From `SPEC.md` §36 — the MVP covers phases **1–15**:

| #  | Component                       | Status      |
| -- | ------------------------------- | ----------- |
| 1  | Base RPC connection             | ✅ done     |
| 2  | Block listener                  | ✅ done     |
| 3  | Contract deployment detection   | ⏳ next     |
| 4  | ERC-20 detection                | ⏳          |
| 5  | Token database                  | ⏳          |
| 6  | DEX / liquidity discovery       | ⏳          |
| 7  | Liquidity monitoring            | ⏳          |
| 8  | Contract security analyzer      | ⏳          |
| 9  | Holder analyzer                 | ⏳          |
| 10 | Deployer history                | ⏳          |
| 11 | Wallet relationship graph       | ⏳          |
| 12 | Behavior / event detection      | ⏳          |
| 13 | Rule-based risk engine          | ⏳          |
| 14 | FastAPI                         | 🟡 skeleton |
| 15 | React dashboard                 | ⏳          |

Phases 16–20 are post-MVP.

Legend: ✅ done · 🟡 partial · ⏳ not started

---

## 🎬 Demo scenarios (acceptance tests)

1. **Clean launch** — a legitimate Base memecoin. Risk = Low. No critical signals.
2. **Soft rug** — deployer renounces, then quietly removes ~30% of LP over an
   hour. Risk climbs to **High** within minutes of LP removal.
3. **Hard rug** — deployer has 5 prior rugs, mints, dumps, removes LP. Risk
   reaches **Critical** in <60s.
4. **Locked liquidity + renounced** — should stay **Low** even if other signals
   are noisy.
5. **Live feed** — refreshes within 5s of a new event.

Each scenario reproducible from a known address + a recorded block.

---

## ⚠️ Risks & open questions

- **RPC rate limits** — `mainnet.base.org` is free but throttled. Need a paid
  fallback URL in `.env.example` (Alchemy / QuickNode / Infura all serve Base).
- **DEX coverage** — Base has many DEXes. MVP targets the top 2–3; the rest
  come later.
- **Holder reconstruction** — built from Transfer events, not `eth_getStorageAt`.
  Slow on day-1 for tokens with thousands of transfers. Caching is mandatory.
- **False positives** — expect noise on low-liquidity legit tokens. Better to
  ship with a tunable threshold than over-tune early.
- **Single point of failure** — listener crash should resume from checkpoint
  (already implemented). API restart resilience TBD.

---

## 📅 Definition of done

- [ ] All 13 MVP criteria pass the demo scenarios above.
- [ ] Risk score always returns `score + level + reasons[]`.
- [ ] `docker compose up` brings the whole stack online.
- [ ] p95 latency from block → dashboard <30s.
- [ ] No `print()` in production code (structured logging only).
- [ ] README updated with run instructions.
- [ ] `SPEC.md` §35 boxes all checked.

---

## 📚 References

- [`SPEC.md`](./SPEC.md) — full project spec, all 37 sections
- [`README.md`](./README.md) — repo orientation
- [`.conversations/`](./.conversations) — session-by-session working log
- Phase build order — `SPEC.md` §36