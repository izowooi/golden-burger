"""Dynamic portfolio account-set contract."""

import pytest

from polybot_reporter.contracts import (
    PORTFOLIO_REPORT_SCHEMA_VERSION,
    PortfolioContractError,
    stable_account_id,
    validate_account_display_names,
)


def test_contract_accepts_double_digit_account_sets() -> None:
    names = [f"GOLDEN-ACCOUNT-{index}" for index in range(1, 21)]

    assert PORTFOLIO_REPORT_SCHEMA_VERSION == "pb-portfolio/v3"
    validate_account_display_names(names)


def test_contract_rejects_duplicate_or_empty_names() -> None:
    with pytest.raises(PortfolioContractError, match="중복"):
        validate_account_display_names(["golden-eagle", " GOLDEN-EAGLE "])

    with pytest.raises(PortfolioContractError, match="비어"):
        validate_account_display_names([" "])


def test_stable_account_id_preserves_duplicate_instance_identity() -> None:
    assert stable_account_id("GOLDEN-APPLE (1)") == "golden-apple-1"
    assert stable_account_id("golden-eagle") == "golden-eagle"
