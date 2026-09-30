"""Public storage ownership for accountless Cherry shadow, never live ledgers."""
import json
import os
from pathlib import Path

from polybot_observability.market_data_raw_links import (
    initialize_raw_links, raw_layout_metadata, require_raw_capabilities,
    validate_raw_source_schema, verify_raw_dependencies,
)
from polybot_observability.market_data_raw_profiles import CHERRY_SHADOW_PROFILE_ID, raw_profile
from polybot_observability.market_data_scalar_links import _namespace, scalar_namespace

from . import RUNTIME_JOB


class SharedRawRuntime:
    def __init__(self,path,references,*,profile_id=None,namespace=None):
        if profile_id not in (None,CHERRY_SHADOW_PROFILE_ID):
            raise ValueError('Cherry shadow RAW profile differs from research schema')
        self.path=Path(path).expanduser().absolute()
        self.references,self.explicit_namespace=references,namespace
        self.enabled=profile_id is not None or namespace is not None or os.environ.get('PUBLIC_MARKET_DATA_RAW')=='1'

    def namespace(self):
        if self.explicit_namespace is not None:
            value=_namespace(self.explicit_namespace,'golden-cherry')
        else:
            if (self.path.resolve()!=self.path or self.path.name!='trades_sim.db'
                    or self.path.parent.parent.name!='data' or self.path.parent.name!=RUNTIME_JOB):
                raise ValueError('Cherry shadow RAW requires canonical runtime path or explicit namespace')
            value=scalar_namespace(os.environ.get('PUBLIC_MARKET_DATA_SOURCE'),os.environ.get('JOB_NAME'),
                                   'golden-cherry',RUNTIME_JOB)
        self._owner(value)
        return value

    @staticmethod
    def _owner(namespace):
        owner=json.loads(namespace)
        if (owner['runtime'],owner['jenkins_job'])!=(RUNTIME_JOB,'polybot-cherry-shadow'):
            raise ValueError('Cherry shadow RAW runtime/Jenkins owner differs')
        if any(os.environ.get(key) is not None and os.environ[key]!=owner[field]
               for key,field in (('PUBLIC_MARKET_DATA_SOURCE','source'),('JOB_NAME','jenkins_job'))):
            raise ValueError('Cherry shadow RAW launcher differs from bound owner')

    def check(self,connection,*,write=False):
        metadata=raw_layout_metadata(connection)
        if metadata is None:
            if not write or not self.enabled:return None
            validate_raw_source_schema(connection,profile_id=CHERRY_SHADOW_PROFILE_ID)
            if any(connection.execute(f'SELECT 1 FROM main."{table}" LIMIT 1').fetchone()
                   for table in raw_profile(CHERRY_SHADOW_PROFILE_ID).tables):
                raise ValueError('populated Cherry shadow requires offline derivative migration')
            namespace=self.namespace()
        else:
            if metadata['profile_id']!=CHERRY_SHADOW_PROFILE_ID:
                raise ValueError('Cherry shadow RAW profile differs from research schema')
            validate_raw_source_schema(connection,profile_id=CHERRY_SHADOW_PROFILE_ID)
            namespace=metadata['namespace'];self._owner(namespace)
            if (write or self.explicit_namespace is not None) and namespace!=self.namespace():
                raise ValueError('Cherry shadow RAW namespace differs')
            if self.references.reader is None or self.references.reader.scalar_authority_identity()!=metadata['authority_uuid']:
                raise ValueError('Cherry shadow RAW reader authority differs')
            self.enabled=True
        if write:
            refs=self.references
            require_raw_capabilities(refs.writer,profile_id=CHERRY_SHADOW_PROFILE_ID)
            if refs.reader is None or refs.reader.scalar_authority_identity()!=refs.writer.scalar_authority_identity():
                raise ValueError('Cherry shadow RAW reader/writer authorities differ')
            if metadata is not None and refs.writer.scalar_authority_identity()!=metadata['authority_uuid']:
                raise ValueError('Cherry shadow RAW writer authority differs')
        return namespace

    def initialize(self,connection):
        if self.enabled:
            namespace=self.check(connection,write=True)
            if not connection.in_transaction:connection.execute('BEGIN IMMEDIATE')
            initialize_raw_links(connection,namespace,self.references.writer.scalar_authority_identity(),
                                 references=self.references,profile_id=CHERRY_SHADOW_PROFILE_ID)


def verify_read_closure(connection,path,references):
    """Offline analysis validates all required public parts, not just its SELECTs."""
    metadata=raw_layout_metadata(connection)
    if metadata is not None:
        SharedRawRuntime(path,references).check(connection)
        verify_raw_dependencies(connection,references=references)
    from polybot_observability.market_data_bundle import reference_closure,verify_closure
    closure=reference_closure(path,'golden-cherry',immutable=True)
    if closure:
        verify_closure(references.reader,closure)
