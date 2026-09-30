"""Read-only Guava closure verification for pinned health and replay adapters.

An external frozen replay adapter must explicitly adopt this connection and pin
its new source SHA. Adding this helper does not rewrite historical adapter pins.
"""
from contextlib import contextmanager
import json
from pathlib import Path

from .market_data_raw_links import raw_layout_metadata, validate_raw_source_schema, verify_raw_dependencies
from .market_data_raw_profiles import GUAVA_PROFILE_ID
from .market_data_refs import configured_references


def verify_guava_read_closure(connection, path, *, references=None, immutable=False):
    metadata = raw_layout_metadata(connection)
    if metadata is None:
        return False
    if metadata['profile_id'] != GUAVA_PROFILE_ID:
        raise ValueError('Guava reader requires its reviewed RAW profile')
    validate_raw_source_schema(connection,profile_id=GUAVA_PROFILE_ID)
    runtime = connection.execute('SELECT job_name FROM main.collection_contracts WHERE singleton=1').fetchone()[0]
    jobs = {f'guava-research-{letter}-v1':f'polybot-sim-guava-{letter}' for letter in 'abcd'}
    owner = json.loads(metadata['namespace'])
    if (owner['runtime'],owner['jenkins_job']) != (runtime,jobs.get(runtime)):
        raise ValueError('Guava RAW reader runtime/Jenkins owner differs')
    refs = references or getattr(connection,'_references',None) or configured_references()
    if refs.reader is None or refs.reader.scalar_authority_identity() != metadata['authority_uuid']:
        raise ValueError('Guava RAW reader authority differs')
    verify_raw_dependencies(connection,references=refs)
    from .market_data_bundle import reference_closure, verify_closure
    verify_closure(refs.reader,reference_closure(path,'golden-guava',immutable=immutable))
    return True


@contextmanager
def guava_read_connection(path, *, references=None, immutable=False):
    """Public-aware cold reader; missing dependencies fail before rows are used."""
    from .market_data_sqlite import connect
    path = Path(path).resolve(strict=True)
    suffix = '?mode=ro&immutable=1' if immutable else '?mode=ro'
    connection = connect(path.as_uri()+suffix,uri=True,references=references)
    try:
        connection.execute('PRAGMA query_only=ON')
        verify_guava_read_closure(connection,path,references=references,immutable=immutable)
        yield connection
    finally:
        connection.close()
