import pytest

from geocatalog.access import (
    DEV_USER_PASSWORDS,
    hash_password,
    has_role_at_least,
    policy_for_role,
    serialize_access_policy,
    token_cost,
    verify_password,
)


def test_role_hierarchy_from_lowest_to_highest_access():
    assert has_role_at_least("explorer", "explorer")
    assert has_role_at_least("mage", "explorer")
    assert has_role_at_least("sage", "mage")
    assert has_role_at_least("god", "sage")
    assert not has_role_at_least("explorer", "mage")
    assert not has_role_at_least("mage", "sage")
    assert not has_role_at_least("sage", "god")


def test_role_capabilities_match_access_model():
    explorer = policy_for_role("explorer")
    mage = policy_for_role("mage")
    sage = policy_for_role("sage")
    god = policy_for_role("god")

    assert explorer.can_filter
    assert explorer.can_view_dataset_rail
    assert explorer.can_view_dataset_detail
    assert explorer.can_view_status_by_platform
    assert not explorer.can_view_full_detail_rail
    assert not explorer.can_access_assets

    assert mage.can_access_assets
    assert mage.uses_tokens
    assert mage.default_tokens == 5000

    assert sage.can_access_assets
    assert not sage.uses_tokens
    assert god.can_access_assets
    assert not god.uses_tokens


def test_mage_token_costs_apply_only_to_mage():
    assert token_cost("search", "mage") == 1
    assert token_cost("download", "mage") == 10
    assert token_cost("stac_asset", "mage") == 5
    assert token_cost("odc_asset", "mage") == 5

    assert token_cost("download", "sage") == 0
    assert token_cost("download", "god") == 0


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError):
        policy_for_role("researcher")


def test_access_policy_serialization_exposes_role_order():
    policy = serialize_access_policy()

    assert policy["role_order"] == ["explorer", "mage", "sage", "god"]
    assert policy["mage_token_costs"]["download"] == 10


def test_password_hashes_are_verifiable_and_salted():
    encoded = hash_password(DEV_USER_PASSWORDS["mage"])
    second = hash_password(DEV_USER_PASSWORDS["mage"])

    assert encoded != DEV_USER_PASSWORDS["mage"]
    assert encoded != second
    assert verify_password(DEV_USER_PASSWORDS["mage"], encoded)
    assert not verify_password("wrong-password", encoded)
