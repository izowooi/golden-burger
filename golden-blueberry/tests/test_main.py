"""CLI safety contracts for Golden Blueberry."""

from polybot.main import (
    _inspection_simulation_override,
    _parser,
    _run_simulation_override,
)


def test_run_without_mode_flag_always_overrides_yaml_to_simulation():
    args = _parser().parse_args(["run"])

    assert _run_simulation_override(args) is True


def test_only_explicit_live_flag_disables_simulation():
    parser = _parser()

    assert _run_simulation_override(parser.parse_args(["run", "--simulate"])) is True
    assert _run_simulation_override(parser.parse_args(["run", "--live"])) is False
    assert _run_simulation_override(parser.parse_args(["run", "--shadow"])) is True


def test_config_and_status_can_select_the_same_runtime_database():
    parser = _parser()

    assert _inspection_simulation_override(
        parser.parse_args(["config", "--live"])
    ) is False
    assert _inspection_simulation_override(
        parser.parse_args(["status", "--simulate"])
    ) is True
    assert _inspection_simulation_override(parser.parse_args(["status"])) is None
    assert _inspection_simulation_override(
        parser.parse_args(["config", "--shadow"])
    ) is True
