"""Dynamic ACCOUNT_<slot> discovery and display-name assignment."""

import logging

import pytest

from polybot_reporter.account_config import AccountConfigurationError, load_account_configs


def test_discovers_sparse_slots_above_old_nine_account_cap():
    accounts = load_account_configs(
        {
            "ACCOUNT_1_NAME": " golden-apple ",
            "ACCOUNT_1_ADDRESS": " 0x1 ",
            "ACCOUNT_4_NAME": "golden-apple",
            "ACCOUNT_4_ADDRESS": "0x4",
            "ACCOUNT_12_NAME": "golden-fox",
            "ACCOUNT_12_ADDRESS": "0x12",
            "ACCOUNT_NOT_A_SLOT_NAME": "ignored",
        }
    )

    assert [account.address for account in accounts] == ["0x1", "0x4", "0x12"]
    assert [account.display_name for account in accounts] == [
        "golden-apple (1)",
        "golden-apple (2)",
        "golden-fox",
    ]


def test_rejects_incomplete_pair_even_with_later_complete_slot():
    with pytest.raises(AccountConfigurationError, match="설정이 불완전"):
        load_account_configs(
            {
                "ACCOUNT_2_NAME": "golden-banana",
                "ACCOUNT_10_NAME": "golden-fox",
                "ACCOUNT_10_ADDRESS": "0x10",
            }
        )


def test_rejects_duplicate_wallet_addresses_case_insensitively():
    with pytest.raises(AccountConfigurationError, match="동일한 wallet address"):
        load_account_configs(
            {
                "ACCOUNT_1_NAME": "golden-apple",
                "ACCOUNT_1_ADDRESS": "0xAbC",
                "ACCOUNT_2_NAME": "golden-banana",
                "ACCOUNT_2_ADDRESS": "0xabc",
            }
        )


def test_wallet_address_is_never_logged_or_exposed_by_repr(caplog):
    wallet = "0x" + "a" * 40

    with caplog.at_level(logging.INFO):
        account = load_account_configs(
            {"ACCOUNT_1_NAME": "golden-fox", "ACCOUNT_1_ADDRESS": wallet}
        )[0]

    assert wallet not in caplog.text
    assert wallet[:10] not in caplog.text
    assert wallet not in repr(account)
    assert "[REDACTED]" in repr(account)
