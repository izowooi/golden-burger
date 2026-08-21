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
    assert [account.slack_name for account in accounts] == [
        "golden-apple (1)",
        "golden-apple (2)",
        "golden-fox",
    ]


def test_slack_name_alias_does_not_change_stable_display_identity():
    accounts = load_account_configs(
        {
            "ACCOUNT_1_NAME": "golden-apple",
            "ACCOUNT_1_ADDRESS": "0x1",
            "ACCOUNT_4_NAME": "golden-apple",
            "ACCOUNT_4_ADDRESS": "0x4",
            "ACCOUNT_4_SLACK_NAME": "orange",
        }
    )

    assert [account.display_name for account in accounts] == [
        "golden-apple (1)",
        "golden-apple (2)",
    ]
    assert [account.slack_name for account in accounts] == [
        "golden-apple (1)",
        "orange",
    ]


def test_rejects_duplicate_slack_names_case_insensitively():
    with pytest.raises(AccountConfigurationError, match="Slack 계정 표시 이름이 중복"):
        load_account_configs(
            {
                "ACCOUNT_1_NAME": "golden-apple",
                "ACCOUNT_1_ADDRESS": "0x1",
                "ACCOUNT_2_NAME": "golden-banana",
                "ACCOUNT_2_ADDRESS": "0x2",
                "ACCOUNT_2_SLACK_NAME": " GOLDEN-APPLE ",
            }
        )


def test_discovers_twenty_account_slots_in_numeric_order():
    environ = {
        f"ACCOUNT_{slot}_{field}": (
            f"golden-{slot}" if field == "NAME" else f"0x{slot:040x}"
        )
        for slot in range(1, 21)
        for field in ("NAME", "ADDRESS")
    }

    accounts = load_account_configs(environ)

    assert len(accounts) == 20
    assert accounts[9].display_name == "golden-10"
    assert accounts[-1].display_name == "golden-20"


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


def test_explicit_report_order_is_applied_after_duplicate_names_are_numbered():
    slot_names = [
        "golden-apple",
        "golden-banana",
        "golden-cherry",
        "golden-apple",
        "golden-eco",
        "golden-fox",
        "golden-lion",
        "golden-tiger",
        "golden-wolf",
        "golden-eagle",
        "golden-bear",
        "golden-cat",
        "golden-dog",
        "golden-queen",
        "golden-king",
        "golden-fruit",
    ]
    expected_order = [
        "golden-apple (1)",
        "golden-banana",
        "golden-cherry",
        "golden-apple (2)",
        "golden-eagle",
        "golden-fox",
        "golden-cat",
        "golden-dog",
        "golden-queen",
        "golden-king",
        "golden-bear",
        "golden-eco",
        "golden-tiger",
        "golden-fruit",
        "golden-lion",
        "golden-wolf",
    ]
    environ = {
        f"ACCOUNT_{slot}_{field}": (
            name if field == "NAME" else f"0x{slot:040x}"
        )
        for slot, name in enumerate(slot_names, start=1)
        for field in ("NAME", "ADDRESS")
    }
    environ["REPORT_ACCOUNT_ORDER"] = ",".join(expected_order)

    accounts = load_account_configs(environ)

    assert [account.display_name for account in accounts] == expected_order


@pytest.mark.parametrize(
    "report_order, error_pattern",
    [
        ("golden-apple,golden-apple", "중복 계정 이름"),
        ("golden-apple,golden-unknown", "정확한 순열"),
        ("golden-apple,", "빈 계정 이름"),
    ],
)
def test_rejects_invalid_explicit_report_order(report_order, error_pattern):
    with pytest.raises(AccountConfigurationError, match=error_pattern):
        load_account_configs(
            {
                "ACCOUNT_1_NAME": "golden-apple",
                "ACCOUNT_1_ADDRESS": "0x1",
                "ACCOUNT_2_NAME": "golden-banana",
                "ACCOUNT_2_ADDRESS": "0x2",
                "REPORT_ACCOUNT_ORDER": report_order,
            }
        )
