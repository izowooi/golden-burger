"""Keep catalog ORM mutations at their original transaction/flush boundary."""

from __future__ import annotations

from sqlalchemy.orm.exc import StaleDataError

from .market_data_catalog_profiles import catalog_profile
from .market_data_orm_flush import bind_model_values, register_shared_flush


def _references(connection):
    from .market_data_refs import configured_references

    return getattr(connection, "_references", None) or configured_references()


def install_catalog_flush(factory, model, strategy):
    """Register catalog new/dirty rows; return the supplied session factory."""
    profile = catalog_profile(strategy)
    if (
        model.__table__.name != "market_catalog"
        or tuple(model.__table__.columns.keys()) != profile.column_names
    ):
        raise ValueError("catalog ORM model differs from reviewed source columns")

    def enabled(connection):
        from .market_data_catalog_links import catalog_layout_metadata

        refs = _references(connection)
        return (
            refs.writer is not None
            or refs.reader is not None
            or catalog_layout_metadata(connection) is not None
        )

    def publish(session, values, is_new):
        from .market_data_catalog_links import (
            MissingCatalogRowError,
            update_shared_catalog,
            upsert_shared_catalog,
        )

        connection = session.connection().connection.driver_connection
        bound = bind_model_values(session, model, values, profile.public_columns)
        if is_new:
            return upsert_shared_catalog(
                connection,
                strategy,
                bound,
                references=_references(connection),
                insert_only=True,
            )
        condition_id = bound.pop("condition_id")
        try:
            return update_shared_catalog(
                connection,
                strategy,
                condition_id,
                bound,
                references=_references(connection),
            )
        except MissingCatalogRowError as error:
            raise StaleDataError(
                "catalog UPDATE expected one row; the original row is missing"
            ) from error

    register_shared_flush(
        factory, model, enabled=enabled, publish=publish, allow_updates=True
    )
    return factory
