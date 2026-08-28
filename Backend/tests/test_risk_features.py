"""Unit tests for spec sec.11 risk feature derivation.

Pure-function tests -- no DB/network needed, since compute_risk_features
only reads fields off an in-memory ContractRiskFlags instance.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.database.models import ContractRiskFlags
from app.discovery.contract_risk import SELECTOR_GROUPS
from app.services.risk_features import (
    KNOWN_SAFE_SELECTORS,
    WITHDRAW_SELECTORS,
    compute_risk_features,
)


def _flags(**overrides) -> ContractRiskFlags:
    defaults = dict(
        token_address="0x" + "aa" * 20,
        has_mint=False,
        has_blacklist=False,
        has_pause=False,
        has_tax_control=False,
        has_max_tx_control=False,
        has_max_wallet_control=False,
        has_fee_exclusion_control=False,
        has_trading_control=False,
        is_upgradeable_proxy=False,
        has_owner_function=False,
        owner_address=None,
        owner_renounced=None,
        selectors_found="",
        bytecode_size=100,
        analyzed_block=1,
        analyzed_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ContractRiskFlags(**defaults)


class TestOwnerGatedFeatures:
    def test_mint_without_owner_is_zero(self):
        """A mint function with NO detected ownership pattern doesn't
        count as owner_can_mint=1 -- see module docstring: we can't
        confirm gating either way, so we don't claim it."""
        flags = _flags(has_mint=True, has_owner_function=False)
        feats = compute_risk_features(flags)
        assert feats["owner_can_mint"] == 0

    def test_mint_with_owner_is_one(self):
        flags = _flags(has_mint=True, has_owner_function=True)
        feats = compute_risk_features(flags)
        assert feats["owner_can_mint"] == 1

    def test_all_capability_flags_map_correctly(self):
        flags = _flags(
            has_mint=True, has_blacklist=True, has_pause=True,
            has_tax_control=True, has_owner_function=True,
            is_upgradeable_proxy=True,
        )
        feats = compute_risk_features(flags)
        assert feats["owner_can_mint"] == 1
        assert feats["owner_can_blacklist"] == 1
        assert feats["owner_can_pause"] == 1
        assert feats["owner_can_modify_tax"] == 1
        assert feats["upgradeable"] == 1


class TestWithdrawDetection:
    def test_withdraw_selector_in_stored_list_detected(self):
        sel = next(iter(WITHDRAW_SELECTORS))
        flags = _flags(has_owner_function=True, selectors_found=sel)
        feats = compute_risk_features(flags)
        assert feats["owner_can_withdraw"] == 1

    def test_no_withdraw_selector_is_zero(self):
        flags = _flags(has_owner_function=True, selectors_found="0xa9059cbb")
        feats = compute_risk_features(flags)
        assert feats["owner_can_withdraw"] == 0

    def test_withdraw_without_owner_is_zero(self):
        sel = next(iter(WITHDRAW_SELECTORS))
        flags = _flags(has_owner_function=False, selectors_found=sel)
        feats = compute_risk_features(flags)
        assert feats["owner_can_withdraw"] == 0


class TestHiddenPrivilegedFunctions:
    def test_only_known_safe_selectors_yields_zero(self):
        selectors = ",".join(list(KNOWN_SAFE_SELECTORS)[:3])
        flags = _flags(selectors_found=selectors)
        feats = compute_risk_features(flags)
        assert feats["hidden_privileged_functions"] == 0

    def test_known_dangerous_selector_not_counted_as_hidden(self):
        """A mint selector is identified (dangerous), not hidden."""
        mint_sel = SELECTOR_GROUPS["has_mint"][0]
        flags = _flags(has_mint=True, selectors_found=mint_sel)
        feats = compute_risk_features(flags)
        assert feats["hidden_privileged_functions"] == 0

    def test_unrecognized_selector_counts_as_hidden(self):
        weird_selector = "0xdeadbeef"
        assert weird_selector not in KNOWN_SAFE_SELECTORS
        all_dangerous = {s for g in SELECTOR_GROUPS.values() for s in g}
        assert weird_selector not in all_dangerous
        flags = _flags(selectors_found=weird_selector)
        feats = compute_risk_features(flags)
        assert feats["hidden_privileged_functions"] == 1

    def test_multiple_unrecognized_selectors_counted(self):
        flags = _flags(selectors_found="0xdeadbeef,0xfeedface,0x12345678")
        feats = compute_risk_features(flags)
        assert feats["hidden_privileged_functions"] == 3

    def test_empty_selectors_found_string_handled(self):
        flags = _flags(selectors_found="")
        feats = compute_risk_features(flags)
        assert feats["hidden_privileged_functions"] == 0


class TestOwnerRenouncedPassthrough:
    def test_none_stays_none(self):
        flags = _flags(owner_renounced=None)
        feats = compute_risk_features(flags)
        assert feats["owner_renounced"] is None

    def test_true_becomes_one(self):
        flags = _flags(owner_renounced=True)
        feats = compute_risk_features(flags)
        assert feats["owner_renounced"] == 1

    def test_false_becomes_zero(self):
        flags = _flags(owner_renounced=False)
        feats = compute_risk_features(flags)
        assert feats["owner_renounced"] == 0
