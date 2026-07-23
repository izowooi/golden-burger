"""CLI safety contracts for Golden Queen."""

from polybot.main import _parser, _run_simulation_override


def test_run_without_mode_flag_always_overrides_yaml_to_simulation():
    args = _parser().parse_args(["run"])

    assert _run_simulation_override(args) is True


def test_only_explicit_live_flag_disables_simulation():
    parser = _parser()

    assert _run_simulation_override(parser.parse_args(["run", "--simulate"])) is True
    assert _run_simulation_override(parser.parse_args(["run", "--live"])) is False
