"""The retro's apply gate is `pytest`: make sure the suite actually exercises the real registry."""

from polylab import registry
from polylab.autopilot.validator import get_path


def test_real_registry_loads_and_every_family_builds():
    from polylab import strategies

    variants = registry.load_all(include_off=True)
    assert variants
    for v in variants:
        assert v.stake_usdc in registry.STAKE_LADDER and v.stake_usdc <= registry.MAX_STAKE_USDC
        strategies.build(v)
        for name, bound in v.bounds.items():
            cur = get_path(v.params, name)
            if isinstance(cur, (int, float)) and not isinstance(cur, bool):
                assert bound[0] <= cur <= bound[1], f"{v.id}: {name}={cur} outside {bound}"
