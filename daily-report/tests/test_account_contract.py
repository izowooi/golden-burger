"""Current portfolio account-set contract."""

import pytest

from polybot_reporter.contracts import (
    ACCOUNT_ID_BY_DISPLAY_NAME,
    PORTFOLIO_REPORT_SCHEMA_VERSION,
    PortfolioContractError,
    validate_account_display_names,
)

EXPECTED_NINE_ACCOUNT_MAPPING = {
    "GOLDEN-APPLE (1)": "golden-apple-1",
    "GOLDEN-BANANA": "golden-banana",
    "GOLDEN-CHERRY": "golden-cherry",
    "GOLDEN-APPLE (2)": "golden-apple-2",
    "GOLDEN-ECO": "golden-eco",
    "GOLDEN-FOX": "golden-fox",
    "GOLDEN-LION": "golden-lion",
    "GOLDEN-TIGER": "golden-tiger",
    "GOLDEN-WOLF": "golden-wolf",
}


def test_current_contract_accepts_exact_nine_accounts() -> None:
    assert PORTFOLIO_REPORT_SCHEMA_VERSION == "pb-portfolio/v3"
    assert ACCOUNT_ID_BY_DISPLAY_NAME == EXPECTED_NINE_ACCOUNT_MAPPING

    validate_account_display_names(list(EXPECTED_NINE_ACCOUNT_MAPPING))


def test_previous_six_account_set_is_incomplete_for_new_snapshots() -> None:
    previous_six = list(EXPECTED_NINE_ACCOUNT_MAPPING)[:6]

    with pytest.raises(PortfolioContractError, match="9계정 exact set"):
        validate_account_display_names(previous_six)
