"""Composite native publication cannot introduce a second private owner."""

import uuid

import pytest
from polybot_observability.market_data_catalog_links import (
    CONTEXT_TABLE as CATALOG_CONTEXT,
)
from polybot_observability.market_data_catalog_links import (
    LAYOUT_TABLE as CATALOG_LAYOUT,
)
from polybot_observability.market_data_catalog_links import (
    initialize_catalog_links,
    upsert_shared_catalog,
)
from polybot_observability.market_data_projection_closure import projection_identity
from polybot_observability.market_data_projection_links import (
    CONTEXT_TABLE as SNAPSHOT_CONTEXT,
)
from polybot_observability.market_data_projection_links import (
    LAYOUT_TABLE as SNAPSHOT_LAYOUT,
)
from polybot_observability.market_data_projection_links import (
    initialize_projection_links,
    insert_shared_projection_snapshot,
)
from polybot_observability.market_data_projection_profiles import snapshot_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadStore
from test_market_data_catalog_links import make_source, row
from test_market_data_projection_links import schema

STRATEGY = "golden-blueberry"


def owner(strategy=STRATEGY, *, source="source-a"):
    return scalar_namespace(source, "job", strategy, "default")


def publish(connection, refs, kind, *, strategy=STRATEGY, namespace=None):
    namespace = namespace or owner(strategy)
    if kind == "catalog":
        return upsert_shared_catalog(
            connection, strategy, row(strategy), namespace=namespace, references=refs
        )
    values = dict.fromkeys(snapshot_profile(strategy).column_names)
    values.update(id=1, condition_id="condition", probability=0.7)
    return insert_shared_projection_snapshot(
        connection, strategy, values, namespace=namespace, references=refs
    )


@pytest.fixture
def composite(tmp_path):
    path = tmp_path / "private.db"
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(reader=store, writer=store)
        with connect(path, references=refs) as connection:
            make_source(connection, STRATEGY)
            connection.execute(schema(STRATEGY))
            connection.commit()
            yield path, connection, refs


@pytest.mark.parametrize("first", ["catalog", "snapshot"])
@pytest.mark.parametrize("damage", ["namespace", "strategy", "authority", "schema"])
def test_sibling_mismatch_cannot_publish_or_create_auxiliary_tables(
    composite, first, damage
):
    path, connection, refs = composite
    second = "snapshot" if first == "catalog" else "catalog"
    publish(connection, refs, first)
    connection.commit()
    sibling_layout = CATALOG_LAYOUT if first == "catalog" else SNAPSHOT_LAYOUT
    sibling_context = CATALOG_CONTEXT if first == "catalog" else SNAPSHOT_CONTEXT
    absent_prefix = (
        "_public_projection_*" if first == "catalog" else "_public_catalog_*"
    )
    kwargs = {}
    if damage == "namespace":
        kwargs["namespace"] = owner(source="source-b")
    elif damage == "strategy":
        kwargs.update(strategy="golden-melon", namespace=owner("golden-melon"))
    elif damage == "authority":
        connection.execute(
            f"UPDATE {sibling_layout} SET authority_uuid=?", (str(uuid.uuid4()),)
        )
    else:
        connection.execute(
            f"ALTER TABLE {sibling_context} ADD COLUMN unauthorized TEXT"
        )
    connection.commit()
    before = refs.writer.projection_stats()
    with pytest.raises(ValueError, match="ownership differ|auxiliary schema"):
        publish(connection, refs, second, **kwargs)
    assert refs.writer.projection_stats() == before
    assert (
        connection.execute(
            "SELECT name FROM main.sqlite_master WHERE name GLOB ?", (absent_prefix,)
        ).fetchall()
        == []
    )
    untouched = "market_snapshots" if second == "snapshot" else "market_catalog"
    assert connection.execute(f"SELECT COUNT(*) FROM main.{untouched}").fetchone() == (
        0,
    )
    assert connection.execute("SELECT * FROM private_ledger").fetchall() == [
        (7, 1.25, -43.12)
    ]


@pytest.mark.parametrize("first", ["catalog", "snapshot"])
def test_matching_sibling_owner_can_publish_in_either_order(composite, first):
    path, connection, refs = composite
    publish(connection, refs, first)
    publish(connection, refs, "snapshot" if first == "catalog" else "catalog")
    connection.commit()
    identity = projection_identity(path, STRATEGY)
    assert identity["namespace"] == owner()
    assert set(identity["layouts"]) == {"market_catalog", "market_snapshots"}
    assert refs.writer.projection_stats()["record_count"] == 3
    assert connection.execute("SELECT COUNT(*) FROM market_catalog").fetchone() == (1,)
    assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (
        1,
    )


@pytest.mark.parametrize("target", ["catalog", "snapshot"])
def test_existing_layout_initializer_still_checks_its_sibling(composite, target):
    _path, connection, refs = composite
    publish(connection, refs, "catalog")
    publish(connection, refs, "snapshot")
    connection.commit()
    sibling = SNAPSHOT_LAYOUT if target == "catalog" else CATALOG_LAYOUT
    connection.execute(f"UPDATE {sibling} SET namespace=?", (owner(source="source-b"),))
    connection.commit()
    before = refs.writer.projection_stats()
    initializer = (
        initialize_catalog_links if target == "catalog" else initialize_projection_links
    )
    with pytest.raises(ValueError, match="ownership differ"):
        initializer(
            connection, STRATEGY, owner(), refs.reader.scalar_authority_identity()
        )
    assert refs.writer.projection_stats() == before
