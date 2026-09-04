"""Source-level safety boundary for the accountless shadow runtime."""

from __future__ import annotations

import os
from typing import Mapping, Sequence


_CREDENTIAL_ENV_KEYS = frozenset(
    {
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_SIGNATURE_TYPE",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_API_PASSPHRASE",
        "CLOB_API_KEY",
        "CLOB_SECRET",
        "CLOB_PASSPHRASE",
    }
)


def assert_shadow_boundary(
    arguments: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> None:
    """Reject live flags and credential/config override environments immediately."""
    if any(argument == "--live" or argument.startswith("--live=") for argument in arguments):
        raise ValueError("Cherry shadow is order-free; --live is forbidden")
    values = os.environ if env is None else env
    present = sorted(
        key
        for key in values
        if key in _CREDENTIAL_ENV_KEYS
        or key.startswith("POLYMARKET_")
        or key.startswith("CLOB_")
    )
    if present:
        raise ValueError(
            "Cherry shadow refuses credential-bearing environments: "
            + ", ".join(present)
        )
    overrides = sorted(key for key in values if key.startswith("POLYBOT_"))
    if overrides:
        raise ValueError(
            "Cherry shadow config is frozen; POLYBOT_* overrides are forbidden: "
            + ", ".join(overrides)
        )
