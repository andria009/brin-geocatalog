from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal

Role = Literal["explorer", "mage", "sage", "god"]
Activity = Literal["search", "download", "stac_asset", "odc_asset"]

ROLE_ORDER: tuple[Role, ...] = ("explorer", "mage", "sage", "god")
ROLE_RANK: dict[Role, int] = {role: index for index, role in enumerate(ROLE_ORDER)}

MAGE_TOKEN_COSTS: dict[Activity, int] = {
    "search": 1,
    "download": 10,
    "stac_asset": 5,
    "odc_asset": 5,
}

PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 260_000

DEV_USER_PASSWORDS: dict[Role, str] = {
    "explorer": "Explorer123!",
    "mage": "Mage123!",
    "sage": "Sage123!",
    "god": "God123!",
}


@dataclass(frozen=True)
class RolePolicy:
    role: Role
    rank: int
    can_filter: bool
    can_view_dataset_rail: bool
    can_view_dataset_detail: bool
    can_view_status_by_platform: bool
    can_view_full_detail_rail: bool
    can_access_assets: bool
    uses_tokens: bool
    default_tokens: int | None = None


ROLE_POLICIES: dict[Role, RolePolicy] = {
    "explorer": RolePolicy(
        role="explorer",
        rank=ROLE_RANK["explorer"],
        can_filter=True,
        can_view_dataset_rail=True,
        can_view_dataset_detail=True,
        can_view_status_by_platform=True,
        can_view_full_detail_rail=False,
        can_access_assets=False,
        uses_tokens=False,
    ),
    "mage": RolePolicy(
        role="mage",
        rank=ROLE_RANK["mage"],
        can_filter=True,
        can_view_dataset_rail=True,
        can_view_dataset_detail=True,
        can_view_status_by_platform=True,
        can_view_full_detail_rail=True,
        can_access_assets=True,
        uses_tokens=True,
        default_tokens=5000,
    ),
    "sage": RolePolicy(
        role="sage",
        rank=ROLE_RANK["sage"],
        can_filter=True,
        can_view_dataset_rail=True,
        can_view_dataset_detail=True,
        can_view_status_by_platform=True,
        can_view_full_detail_rail=True,
        can_access_assets=True,
        uses_tokens=False,
    ),
    "god": RolePolicy(
        role="god",
        rank=ROLE_RANK["god"],
        can_filter=True,
        can_view_dataset_rail=True,
        can_view_dataset_detail=True,
        can_view_status_by_platform=True,
        can_view_full_detail_rail=True,
        can_access_assets=True,
        uses_tokens=False,
    ),
}


def normalize_role(value: str | None) -> Role:
    role = (value or "explorer").strip().lower()
    if role not in ROLE_POLICIES:
        raise ValueError(f"Unsupported GeoCatalog role: {value}")
    return role  # type: ignore[return-value]


def role_rank(role: str | None) -> int:
    return ROLE_RANK[normalize_role(role)]


def has_role_at_least(role: str | None, minimum: Role) -> bool:
    return role_rank(role) >= ROLE_RANK[minimum]


def policy_for_role(role: str | None) -> RolePolicy:
    return ROLE_POLICIES[normalize_role(role)]


def token_cost(activity: Activity, role: str | None) -> int:
    if normalize_role(role) != "mage":
        return 0
    return MAGE_TOKEN_COSTS[activity]


def hash_password(password: str, *, salt: str | None = None) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_value.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}${salt_value}${digest}"


def verify_password(password: str, encoded_hash: str | None) -> bool:
    if not password or not encoded_hash:
        return False
    try:
        algorithm, iterations_text, salt, expected_digest = encoded_hash.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False
    if algorithm != PASSWORD_HASH_ALGORITHM:
        return False
    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(actual_digest, expected_digest)


def serialize_access_policy() -> dict:
    return {
        "role_order": list(ROLE_ORDER),
        "mage_token_costs": dict(MAGE_TOKEN_COSTS),
        "sample_passwords_available": True,
        "roles": [
            {
                "role": policy.role,
                "rank": policy.rank,
                "can_filter": policy.can_filter,
                "can_view_dataset_rail": policy.can_view_dataset_rail,
                "can_view_dataset_detail": policy.can_view_dataset_detail,
                "can_view_status_by_platform": policy.can_view_status_by_platform,
                "can_view_full_detail_rail": policy.can_view_full_detail_rail,
                "can_access_assets": policy.can_access_assets,
                "uses_tokens": policy.uses_tokens,
                "default_tokens": policy.default_tokens,
            }
            for policy in ROLE_POLICIES.values()
        ],
    }
