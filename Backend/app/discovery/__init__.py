"""Discovery layer: extract on-chain entities from raw blocks.

Phase 3 currently contains only the contract-deployment detector. ERC-20
detection (Phase 4), liquidity discovery (Phase 6) and later detectors
will live here as additional ``on_block`` callbacks.
"""

