"""Storage ownership for recorder source projections, separate from v6 history."""
import json
import os
from pathlib import Path

from polybot_observability.market_data_raw_links import (
    initialize_raw_links, raw_layout_metadata, require_raw_capabilities,
    validate_raw_source_schema,
)
from polybot_observability.market_data_raw_profiles import COCONUT_RECORDER_PROFILE_ID, raw_profile
from polybot_observability.market_data_scalar_links import _namespace, scalar_namespace


RUNTIME_JOBS = {
    'coconut-sports-recorder-1m-v1': 'polybot-white',
    'coconut-sports-recorder-1m-v2': 'polybot-white',
    'coconut-sports-recorder-silver-1m-v1': 'polybot-silver',
}


class RecorderSharedRaw:
    def __init__(self, path, runtime, references, *, profile_id=None, namespace=None):
        if profile_id not in (None, COCONUT_RECORDER_PROFILE_ID):
            raise ValueError('recorder RAW profile differs from native schema')
        self.path = Path(path).expanduser().absolute()
        self.runtime, self.references, self.explicit_namespace = runtime, references, namespace
        self.enabled = profile_id is not None or namespace is not None or os.environ.get('PUBLIC_MARKET_DATA_RAW') == '1'

    def namespace(self):
        if self.explicit_namespace is not None:
            value = _namespace(self.explicit_namespace, 'golden-coconut')
        else:
            if (self.path.resolve() != self.path or self.path.name != 'trades_sim.db'
                    or self.path.parent.parent.name != 'data' or self.path.parent.name != self.runtime):
                raise ValueError('recorder RAW needs its canonical runtime path or explicit namespace')
            value = scalar_namespace(os.environ.get('PUBLIC_MARKET_DATA_SOURCE'), os.environ.get('JOB_NAME'),
                                     'golden-coconut', self.runtime)
        self._check_owner(value)
        return value

    def _check_owner(self, namespace):
        owner = json.loads(namespace)
        if self.runtime not in RUNTIME_JOBS or (owner['runtime'], owner['jenkins_job']) != (self.runtime, RUNTIME_JOBS[self.runtime]):
            raise ValueError('recorder RAW runtime/Jenkins owner differs')
        if any(os.environ.get(key) is not None and os.environ[key] != owner[field]
               for key, field in (('PUBLIC_MARKET_DATA_SOURCE','source'), ('JOB_NAME','jenkins_job'))):
            raise ValueError('recorder RAW launcher differs from bound owner')

    def check(self, connection, *, write=False):
        metadata = raw_layout_metadata(connection)
        if metadata is None:
            if not write or not self.enabled:
                return None
            validate_raw_source_schema(connection, profile_id=COCONUT_RECORDER_PROFILE_ID)
            if any(connection.execute(f'SELECT 1 FROM main."{table}" LIMIT 1').fetchone()
                   for table in raw_profile(COCONUT_RECORDER_PROFILE_ID).tables):
                raise ValueError('populated recorder requires explicit offline derivative migration')
            namespace = self.namespace()
        else:
            if metadata['profile_id'] != COCONUT_RECORDER_PROFILE_ID:
                raise ValueError('recorder RAW profile differs from native schema')
            validate_raw_source_schema(connection, profile_id=COCONUT_RECORDER_PROFILE_ID)
            namespace = metadata['namespace']
            self._check_owner(namespace)
            if (write or self.explicit_namespace is not None) and namespace != self.namespace():
                raise ValueError('recorder RAW namespace differs')
            if self.references.reader is None or self.references.reader.scalar_authority_identity() != metadata['authority_uuid']:
                raise ValueError('recorder RAW reader authority differs')
            self.enabled = True
        if write:
            refs = self.references
            require_raw_capabilities(refs.writer, profile_id=COCONUT_RECORDER_PROFILE_ID)
            if refs.reader is None or refs.reader.scalar_authority_identity() != refs.writer.scalar_authority_identity():
                raise ValueError('recorder RAW reader/writer authorities differ')
            if metadata is not None and refs.writer.scalar_authority_identity() != metadata['authority_uuid']:
                raise ValueError('recorder RAW writer authority differs')
        return namespace

    def initialize(self, connection):
        if self.enabled:
            namespace = self.check(connection, write=True)
            initialize_raw_links(connection, namespace, self.references.writer.scalar_authority_identity(),
                                 references=self.references, profile_id=COCONUT_RECORDER_PROFILE_ID)
