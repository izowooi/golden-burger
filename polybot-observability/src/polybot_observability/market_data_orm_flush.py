"""One transactional SQLAlchemy flush boundary for shared public storage adapters.

Only explicitly registered session factories/models participate. State changes use
SQLAlchemy's public events and attribute APIs; private ledger objects continue
through the normal unit of work.
"""

from __future__ import annotations

import uuid
from weakref import WeakKeyDictionary

from sqlalchemy import event, inspect
from sqlalchemy.orm import attributes, make_transient, make_transient_to_detached

_REGISTRATIONS = WeakKeyDictionary()
_TRACKING = "polybot_public_storage_flush"


def bind_model_values(session, model, values, public_columns=()):
    """Apply original SQLite affinities while avoiding redundant public body CAS."""
    from .market_data_sqlalchemy import original_public_type

    dialect = session.get_bind().dialect
    result = {}
    for name, value in values.items():
        source_type = model.__table__.columns[name].type
        if name in public_columns:
            source_type = original_public_type(source_type)
        processor = source_type.dialect_impl(dialect).bind_processor(dialect)
        result[name] = processor(value) if processor else value
    return result


def _loaded_values(instance, fallback):
    values = dict(fallback)
    for name, attribute in inspect(instance).attrs.items():
        if name in values and attribute.loaded_value is not attributes.NO_VALUE:
            values[name] = attribute.loaded_value
    return values


def _restore_dirty(session, instance, original, histories):
    session.expire(instance)
    for name, value in original.items():
        history = histories[name]
        attributes.set_committed_value(
            instance, name, history.deleted[0] if history.deleted else value
        )
        if history.has_changes():
            setattr(instance, name, value)
            if not history.deleted:
                attributes.flag_modified(instance, name)


def register_shared_flush(factory, model, *, enabled, publish, allow_updates=False):
    """Register one model's new/dirty publication within the factory's flush.

    ``publish(session, values, is_new)`` receives Python values: all new columns
    (defaults evaluated at flush), or changed columns plus the immutable PK for
    dirty rows. It returns a complete normalized DB row. ``enabled(connection)``
    leaves ordinary legacy sessions on their original ORM path.
    """
    handlers = _REGISTRATIONS.get(factory)
    if handlers is None:
        handlers = []
        _REGISTRATIONS[factory] = handlers
        _install(factory, handlers)
    if any(handler[0] is model for handler in handlers):
        raise ValueError("shared flush model is already registered on this factory")
    handlers.append((model, enabled, publish, allow_updates))


def _install(factory, handlers):
    def discard(session, transaction):
        tracked = session.info.get(_TRACKING, {})
        entry = tracked.pop(transaction, None)
        if entry is not None:
            for instance, is_new, values in entry["rows"]:
                if is_new:
                    restored = _loaded_values(instance, values)
                    make_transient(instance)
                    for name, value in restored.items():
                        setattr(instance, name, value)
                elif inspect(instance).persistent and instance in session:
                    session.expire(instance)
        if not tracked:
            session.info.pop(_TRACKING, None)

    def before_flush(session, flush_context, objects):
        selected = None if objects is None else {id(instance) for instance in objects}
        candidates = []
        for instance in list(session.new) + list(session.dirty):
            if selected is not None and id(instance) not in selected:
                continue
            for handler in handlers:
                if isinstance(instance, handler[0]):
                    is_new = inspect(instance).pending
                    if is_new or handler[3]:
                        candidates.append((instance, is_new, handler))
                    break
        if not candidates:
            return
        connection = session.connection().connection.driver_connection
        active = {}
        for item in candidates:
            handler_id = id(item[2])
            if handler_id not in active:
                active[handler_id] = item[2][1](connection)
        candidates = [item for item in candidates if active[id(item[2])]]
        if not candidates:
            return
        transaction = session.get_nested_transaction() or session.get_transaction()
        tracked = session.info.setdefault(_TRACKING, {}).setdefault(
            transaction, {"rows": [], "committed": False}
        )
        if not connection.in_transaction:
            connection.execute("BEGIN")
        savepoint = "public_flush_" + uuid.uuid4().hex
        connection.execute("SAVEPOINT " + savepoint)
        converted = []
        previous_count = len(tracked["rows"])
        try:
            for instance, is_new, (model, _enabled, publish, _updates) in candidates:
                state = inspect(instance)
                histories = {
                    column.name: state.attrs[column.name].history
                    for column in model.__table__.columns
                }
                original = {
                    column.name: getattr(instance, column.name)
                    for column in model.__table__.columns
                }
                if is_new:
                    values = dict(original)
                    for column in model.__table__.columns:
                        if values[column.name] is None and column.default is not None:
                            default = column.default
                            values[column.name] = (
                                default.arg(None)
                                if default.is_callable
                                else default.arg
                            )
                else:
                    keys = tuple(column.name for column in model.__table__.primary_key)
                    if tuple(original[name] for name in keys) != state.identity:
                        raise ValueError(
                            "shared public storage primary key is immutable"
                        )
                    values = {
                        name: original[name]
                        for name, history in histories.items()
                        if history.has_changes()
                    }
                    if not values:
                        continue
                    values.update((name, original[name]) for name in keys)
                row = publish(session, values, is_new)
                restored = dict(original)
                names = set(row) if is_new else set(values)
                dialect = session.get_bind().dialect
                for name in names:
                    column = model.__table__.columns[name]
                    processor = column.type.dialect_impl(dialect).result_processor(
                        dialect, None
                    )
                    restored[name] = processor(row[name]) if processor else row[name]
                converted.append((instance, is_new, original, histories))
                if is_new:
                    session.expunge(instance)
                    for name, value in restored.items():
                        setattr(instance, name, value)
                    make_transient_to_detached(instance)
                    session.add(instance)
                else:
                    session.expire(instance)
                    for name, value in restored.items():
                        attributes.set_committed_value(instance, name, value)
                tracked["rows"].append([instance, is_new, restored])
            connection.execute("RELEASE " + savepoint)
        except BaseException:
            connection.execute("ROLLBACK TO " + savepoint)
            connection.execute("RELEASE " + savepoint)
            del tracked["rows"][previous_count:]
            for instance, is_new, original, histories in converted:
                if is_new:
                    make_transient(instance)
                    for name, value in original.items():
                        setattr(instance, name, value)
                    session.add(instance)
                else:
                    _restore_dirty(session, instance, original, histories)
            raise

    def after_rollback(session):
        # SQLAlchemy expires persistent objects after this event. Save loaded
        # cells of our inserted objects first, matching normal INSERT rollback.
        for entry in session.info.get(_TRACKING, {}).values():
            for row in entry["rows"]:
                if row[1]:
                    row[2] = _loaded_values(row[0], row[2])

    def after_commit(session):
        transaction = session.get_nested_transaction() or session.get_transaction()
        entry = session.info.get(_TRACKING, {}).get(transaction)
        if entry is not None:
            entry["committed"] = True

    def after_transaction_end(session, transaction):
        tracked = session.info.get(_TRACKING, {})
        entry = tracked.get(transaction)
        if entry is None:
            return
        if not entry["committed"]:
            discard(session, transaction)
        else:
            tracked.pop(transaction)
            if transaction.parent is not None:
                parent = tracked.setdefault(
                    transaction.parent, {"rows": [], "committed": False}
                )
                parent["rows"].extend(entry["rows"])
            if not tracked:
                session.info.pop(_TRACKING, None)

    def after_soft_rollback(session, previous_transaction):
        if previous_transaction.parent is not None and not previous_transaction.nested:
            discard(session, previous_transaction.parent)

    event.listen(factory, "before_flush", before_flush)
    event.listen(factory, "after_rollback", after_rollback)
    event.listen(factory, "after_commit", after_commit)
    event.listen(factory, "after_transaction_end", after_transaction_end)
    event.listen(factory, "after_soft_rollback", after_soft_rollback)
