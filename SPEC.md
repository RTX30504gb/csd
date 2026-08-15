# Real-Time Crypto Rug-Pull Detection Engine

> Canonical project plan. Source of truth for scope, phasing, and architecture.
> Sections 1-37 captured 2026-08-12. The 20-phase development order is in section 36.

## 1. Project Objective

Build a real-time blockchain security system that monitors newly launched ERC-20 tokens on Base, analyzes their smart contracts, liquidity, holders, deployer history, wallet relationships, and trading behavior, and produces an explainable rug-pull risk score.

The system should aim to identify suspicious tokens before a liquidity withdrawal or catastrophic price collapse occurs.

Initial target:

- Blockchain: Base
- Token standard: ERC-20
- Typical analysis latency: <30 seconds
- Hard latency target: <60 seconds
- Initial scoring: Rule-based
- Later scoring: Machine learning
- Backend: Python + FastAPI
- Blockchain interaction: Web3.py
- Database: PostgreSQL
- Cache / fast state: Redis
- Frontend: React + TypeScript
- ML: XGBoost

## 2. High-Level Architecture

```
Base Blockchain
        |
        v
Blockchain Listener
        |
        v
Token Discovery
        |
        v
Data Collection
        |
        v
Feature Extraction
        |
        v
Risk Engine
        |
        v
PostgreSQL
        |
        v
FastAPI
        |
        v
React Dashboard
```

Internal analysis pipeline:

```
New Token
        |
        v
Contract Analysis
        |
        v
Liquidity Analysis
        |
        v
Holder Analysis
        |
        v
Deployer Analysis
        |
        v
Wallet Relationship Analysis
        |
        v
Behavior Analysis
        |
        v
Risk Scoring
        |
        v
Continuous Monitoring
```

## 3. Repository Structure

```
rug-detector/
  backend/
    app/
      main.py
      config.py
      blockchain/
        provider.py
        listener.py
        decoder.py
      discovery/
        tokens.py
      analysis/
        contract.py
        holders.py
        liquidity.py
        behavior.py
      scoring/
        risk.py
      database/
        models.py
        database.py
    tests/
  frontend/
    src/
      pages/
      components/
      api/
```

## 4. Blockchain Connection

Connect the backend to Base through an RPC provider. Use Web3.py.

The blockchain provider needs to allow the application to:

- Get the latest block
- Retrieve blocks
- Retrieve transactions
- Retrieve transaction receipts
- Read contract data
- Read event logs
- Query balances
- Retrieve contract bytecode

The application should process new blocks incrementally instead of repeatedly scanning the entire blockchain.

## 5. Block Listener

Create a blockchain listener. The listener should:

1. Request the latest Base block.
2. Compare it with the last processed block stored in the database.
3. Process all blocks that have not yet been processed.
4. Extract relevant transactions and events.
5. Save the latest processed block.
6. Resume from the saved block after a restart.

Example:

```
Last processed block:  25,099,997
New blocks:            25,099,998
                       25,099,999
                       25,100,000
Process all three.
Then save:
last_processed_block = 25,100,000
```

## 6. Contract Discovery

Inspect transactions in new blocks. A contract deployment transaction can be identified by a transaction with no destination address.

For every deployment:

1. Identify the deployer address.
2. Obtain the transaction receipt.
3. Obtain the newly created contract address.
4. Store the contract address.
5. Inspect the contract to determine whether it behaves like an ERC-20 token.

Do not assume every deployed contract is a token.

## 7. ERC-20 Detection

Query standard ERC-20 functionality:

- `name()`
- `symbol()`
- `decimals()`
- `totalSupply()`

If the contract behaves like an ERC-20, register it as a token.

Store:

- Contract address
- Token name
- Symbol
- Decimals
- Total supply
- Deployer
- Creation block
- Creation timestamp

## 8. Liquidity Discovery

A token becomes more interesting once it is actually tradable. Monitor relevant Base DEX infrastructure.

For each newly discovered token:

1. Search for liquidity pools.
2. Identify the token pair.
3. Identify the pool address.
4. Determine the paired asset.
5. Determine initial liquidity.
6. Begin monitoring liquidity changes.

Store:

- Pool address
- Token address
- Pair asset
- Reserve values
- Initial liquidity
- Current liquidity
- Liquidity changes

## 9. Liquidity Monitoring

Continuously monitor liquidity pools. Track:

- Liquidity added
- Liquidity removed
- Liquidity changes
- LP ownership
- Liquidity lock status
- Lock duration
- Wallet performing liquidity operations

If liquidity changes significantly, generate a liquidity withdrawal event.

Do not automatically classify every withdrawal as a rug. Investigate:

- Who removed it?
- Is that wallet associated with the deployer?
- What percentage was removed?
- Did token price collapse?
- Was the withdrawal expected?
- Did liquidity return?

## 10. Smart Contract Analysis

Analyze the token contract for potentially dangerous capabilities. Look for:

- Unlimited minting
- Blacklisting
- Pausing transfers
- Adjustable taxes
- Adjustable fees
- Maximum transaction restrictions
- Maximum wallet restrictions
- Upgradeability
- Owner-controlled withdrawals
- Trading restrictions
- Hidden privileged functions

Determine:

- Who can call the function?
- What can it change?
- Is there a hard limit?
- Can ownership be transferred?
- Is ownership renounced?
- Is the contract upgradeable?
- Are privileged roles controlled by one wallet or multiple parties?

Example:

```
owner -> setTax(99%)
```

is substantially more dangerous than:

```
owner -> setTax()
```

with a hard-coded maximum tax of 5%.

## 11. Contract Risk Features

Convert contract analysis into numerical features.

Examples:

```
owner_can_mint = 1
owner_can_blacklist = 1
owner_can_pause = 1
owner_can_modify_tax = 1
owner_can_withdraw = 1
upgradeable = 1
hidden_privileged_functions = 1
```

These features will later be used by the risk engine and ML model.

## 12. Holder Analysis

ERC-20 balances can be reconstructed from Transfer events. Process transfer events to determine current token balances.

Calculate:

- Largest holder percentage
- Top 5 holder percentage
- Top 10 holder percentage
- Top 20 holder percentage
- Creator holdings
- Creator-associated wallet holdings

Do not automatically classify high concentration as malicious. First classify infrastructure addresses.

## 13. Address Classification

Important addresses should be classified. Possible categories:

- EOA
- Contract
- DEX pool
- DEX router
- Burn address
- Bridge
- Exchange
- Deployer
- Deployer-associated
- Unknown

A DEX pool holding 40% of supply should not be treated the same way as an unknown wallet holding 40%. Use known addresses and heuristics initially.

## 14. Deployer Analysis

For every token deployer, analyze historical activity. Determine:

- Number of previous contracts
- Number of previous token launches
- Previous suspicious tokens
- Previous liquidity withdrawals
- Previous token collapses
- Funding sources
- Wallet age
- Relationships with other wallets

Example:

```
Wallet A:
  Token 1 -> liquidity removed
  Token 2 -> 98% collapse
  Token 3 -> liquidity removed
  Token 4 -> currently launching
Token 4 should inherit significant deployer risk.
```

## 15. Wallet Relationship Analysis

Build a graph of wallet relationships. Look for:

- Shared funding sources
- Transfers between wallets
- Similar transaction timing
- Coordinated purchases
- Coordinated selling
- Common deployers
- Common historical projects

The goal is to detect clusters of wallets potentially controlled by the same actor.

## 16. Behavioral Analysis

Monitor the token continuously after launch. Important events include:

```
### Creator selling
Deployer
   -> Token transfer
   -> DEX
   -> Sell

### Coordinated selling
Wallet A
Wallet B
Wallet C
   -> Sell

### Liquidity extraction
LP controller
   -> Large liquidity withdrawal

### Supply manipulation
Mint
   -> Large supply increase
   -> Sell
```

Every meaningful behavior becomes an event.

## 17. Event System

Create internal event types:

- `TOKEN_CREATED`
- `LIQUIDITY_ADDED`
- `LIQUIDITY_REMOVED`
- `LARGE_TRANSFER`
- `CREATOR_SELL`
- `MINT_DETECTED`
- `BLACKLIST_DETECTED`
- `TAX_CHANGED`
- `WALLET_CLUSTER_DETECTED`
- `SUSPICIOUS_SELLING`

Each event should contain:

- Timestamp
- Block
- Transaction hash
- Token
- Wallet
- Event type
- Amount
- Supporting evidence

## 18. Feature Extraction

Do not send raw blockchain data directly into the risk model. Convert observations into features.

Example:

```
owner_can_mint = 1
owner_can_blacklist = 1
liquidity_locked = 0
top_10_holder_percentage = 0.73
creator_percentage = 0.18
previous_deployer_rugs = 4
wallet_cluster_size = 11
creator_sold = 0
liquidity_removed_1h = 0
```

These features represent the token's current state.

## 19. Initial Risk Engine

Start with a rule-based system. Create five risk categories:

1. Contract risk
2. Liquidity risk
3. Holder risk
4. Deployer risk
5. Behavioral risk

Each category receives a score. Example:

```
Contract  = 72
Liquidity = 90
Holder    = 64
Deployer  = 85
Behavior  = 40
```

Combine these into an overall score. Example:

```
Risk score = 76 / 100
```

The exact weights should eventually be optimized using historical data.

## 20. Risk Classification

Use these initial levels:

### Low / Normal
Few meaningful risk signals.

### Suspicious
Multiple warning signals exist. Example:

- Liquidity not locked
- High holder concentration
- Owner can modify taxes

### High Risk
Several independent risk signals align. Example:

- Creator controls liquidity
- Deployer has suspicious history
- Wallet cluster detected
- Dangerous contract permissions

### Critical
Evidence of active malicious behavior. Example:

- Large liquidity withdrawal
- Creator-associated wallets selling
- Contract restrictions preventing normal selling

The system should never claim certainty based solely on a risk score.

## 21. Explainable Risk Score

Never return only:

```
87
```

Return:

```
87 / 100 -- CRITICAL

Reasons:
1. Liquidity is not locked.
2. Deployer controls 17.8% of supply.
3. Deployer is associated with four previous suspicious launches.
4. Owner can modify transaction restrictions.
5. Six wallets share funding sources with the deployer.
```

Every risk score should have supporting evidence.

## 22. Database

Use PostgreSQL. Core tables:

- `tokens`
- `contracts`
- `deployers`
- `wallets`
- `holders`
- `pools`
- `transactions`
- `transfers`
- `liquidity_events`
- `risk_scores`
- `risk_events`
- `wallet_relationships`

Store raw evidence as well as derived features. Do not store only the final risk score. The system must be able to answer: "Why did the system give this token a score of 91?" The database should contain the evidence that produced the score.

## 23. Redis

Use Redis for rapidly changing state. Examples:

- Active token monitoring
- Current risk scores
- Recently processed blocks
- Temporary analysis state
- Worker queues
- Recently observed events

PostgreSQL is the persistent source of truth. Redis handles fast-changing state.

## 24. Asynchronous Analysis

Do not analyze everything sequentially. Use workers:

```
New Token
   -> Event Queue
   -> Contract Worker
   -> Holder Worker
   -> Liquidity Worker
   -> Deployer Worker
   -> Behavior Worker
   -> Risk Engine
   -> Database / API
```

The workers operate concurrently. This is necessary for the latency target.

## 25. API

Use FastAPI. Initial endpoints:

```
GET /tokens
GET /tokens/{address}
GET /tokens/{address}/risk
GET /tokens/{address}/holders
GET /tokens/{address}/liquidity
GET /tokens/{address}/deployer
GET /wallets/{address}
GET /wallets/{address}/history
GET /wallets/{address}/relationships
```

Example response:

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

## 26. Frontend

Use React + TypeScript. Create three primary pages.

### Token Page
Display:

- Token name
- Symbol
- Contract address
- Risk score
- Risk category
- Contract risk
- Liquidity risk
- Holder risk
- Deployer risk
- Behavioral risk
- Reasons
- Recent events
- Liquidity chart
- Holder distribution

### Wallet Page
Display:

- Wallet address
- Reputation score
- Tokens deployed
- Previous launches
- Suspicious launches
- Liquidity withdrawals
- Related wallets
- Transaction graph

### Live Feed

```
11:42:03 -- New token detected
11:42:07 -- ERC-20 confirmed
11:42:13 -- Liquidity found
11:42:18 -- Deployer analyzed
11:42:21 -- Risk = 73
11:42:26 -- Wallet cluster detected
11:42:27 -- Risk = 84
```

## 27. Latency Target

Initial target: typical detection <30 seconds. Hard target: <60 seconds.

Target breakdown:

- Block detected: <2 sec
- Transaction parsed: <5 sec
- Feature updates: <10 sec
- Risk calculation: <2 sec
- API / dashboard update: <5 sec

The system should prioritize newly launched tokens and high-risk events.

## 28. Historical Dataset

After the pipeline works, create a historical dataset. Include:

- Known rug pulls
- Known legitimate projects
- Token metadata
- Contract features
- Holder distribution
- Liquidity behavior
- Deployer history
- Wallet relationships
- Trading behavior

Record the state of each token at multiple points:

- T+1 minute
- T+5 minutes
- T+10 minutes
- T+30 minutes
- T+1 hour
- T+6 hours
- T+24 hours

This is critical. The model must only use information that would have been available at the time of prediction. Do not allow future information to leak into earlier predictions.

## 29. ML Model

After collecting enough historical data, train an ML model. Initial model: XGBoost.

- **Input:** token features at a specific time after launch.
- **Output:** probability of a rug within a defined future period.
- Example: `P(rug within 24 hours) = 0.91`

Compare:

1. Rule-based system
2. XGBoost
3. Rule-based + XGBoost

Evaluate all three.

## 30. Model Evaluation

Important metrics:

- Precision
- Recall
- F1 score
- False-positive rate
- ROC-AUC
- Detection rate
- Time-to-detection

The most important product metric is **time-to-warning**. Example:

```
Actual rug:             14:32
First high-risk warning: 13:47
Early warning:           45 minutes
```

The system should be evaluated on how much warning it provides before malicious behavior becomes obvious.

## 31. Avoid Data Leakage

Never train the model using information that happened after the prediction point. For example, if we are trying to predict a rug at T+10 minutes, the model cannot use:

- Later liquidity withdrawal
- Later price collapse
- Later transactions
- Later holder distribution

Only information available at T+10 minutes is allowed. Otherwise the model will look brilliant while being completely useless in production.

## 32. Mechanical Engineering / RWA Extension

Do not add the mechanical-engineering component to the first MVP. Add it later as a Real-World Asset verification module.

Example: a project claims "10,000 industrial machines worth Rs. 500 crore back this token." The system could eventually compare the claim against:

- Machine inventory
- Manufacturer
- Model
- Machine specifications
- Operating hours
- Energy consumption
- Maintenance history
- Production capacity
- IoT telemetry

The system could calculate an asset-integrity score.

Concept:

```
Blockchain evidence + Physical-world evidence -> Asset Integrity Score
```

This creates a connection between:

- Blockchain
- Cybersecurity
- Machine learning
- IoT
- Mechanical engineering
- Physical asset verification

## 33. Final Architecture

```
BASE BLOCKCHAIN
   -> BLOCK LISTENER
   -> TOKEN DISCOVERY
   -> CONTRACT ANALYSIS + LIQUIDITY ANALYSIS + HOLDER ANALYSIS
   -> WALLET INTELLIGENCE
   -> BEHAVIOR ENGINE
   -> RISK ENGINE
   -> RULES + ML
   -> RISK SCORE
   -> FASTAPI + DATABASE
   -> REACT DASHBOARD
```

## 34. Core Detection Loop

The complete system operates continuously:

1. Detect new block.
2. Inspect transactions.
3. Detect newly deployed contracts.
4. Determine whether contracts are ERC-20 tokens.
5. Identify deployer.
6. Find liquidity pools.
7. Analyze contract permissions.
8. Analyze holder distribution.
9. Analyze deployer history.
10. Analyze wallet relationships.
11. Monitor token behavior.
12. Convert observations into features.
13. Calculate risk score.
14. Store evidence.
15. Update API.
16. Update dashboard.
17. Continue monitoring.
18. Recalculate the score when new events occur.
19. Record the eventual outcome.
20. Feed historical outcomes into the future ML dataset.

## 35. MVP Definition

The first version is successful when it can:

1. Monitor Base.
2. Detect newly deployed ERC-20 tokens.
3. Identify the deployer.
4. Find a relevant liquidity pool.
5. Analyze basic contract permissions.
6. Calculate holder concentration.
7. Analyze deployer history.
8. Detect basic wallet relationships.
9. Detect liquidity changes.
10. Produce an explainable risk score.
11. Update the score when new suspicious events occur.
12. Display results through a web dashboard.
13. Achieve typical analysis latency below 30 seconds.

Do not attempt multi-chain support, sophisticated ML, IoT, or physical asset verification until this works reliably.

## 36. Development Order

Build in this exact order:

| Phase | Component                          |
|------:|------------------------------------|
|  1    | Base RPC connection                |
|  2    | Block listener                     |
|  3    | Contract deployment detection      |
|  4    | ERC-20 detection                   |
|  5    | Token database                     |
|  6    | DEX / liquidity discovery          |
|  7    | Liquidity monitoring               |
|  8    | Contract security analyzer        |
|  9    | Holder analyzer                    |
|  10   | Deployer history                   |
|  11   | Wallet relationship graph          |
|  12   | Behavior / event detection         |
|  13   | Rule-based risk engine             |
|  14   | FastAPI                            |
|  15   | React dashboard                    |
|  16   | Latency optimization               |
|  17   | Historical dataset                 |
|  18   | XGBoost model                      |
|  19   | Model evaluation                   |
|  20   | Real-world asset verification ext. |

## 37. Core Research Question

"Can publicly observable on-chain behavior be used to identify elevated rug-pull risk before a liquidity withdrawal or catastrophic price collapse occurs?"

The product should not claim to predict with certainty that a project is a scam. It should provide:

- Evidence
- Risk score
- Explanation
- Historical context
- Real-time behavioral changes
- Time-to-warning

The goal is an early-warning blockchain intelligence system, not a magical scam detector.