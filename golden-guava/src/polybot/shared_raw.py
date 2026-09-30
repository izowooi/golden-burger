"""Guava RAW ownership without changing research/runtime or lifecycle contracts."""
import json
import os
from pathlib import Path

from polybot_observability.market_data_raw_links import (
    initialize_raw_links, raw_layout_metadata, require_raw_capabilities,
    validate_raw_source_schema,
)
from polybot_observability.market_data_raw_profiles import GUAVA_PROFILE_ID, raw_profile
from polybot_observability.market_data_scalar_links import _namespace, scalar_namespace


RESEARCH_JOBS = {f'guava-research-{letter}-v1': f'polybot-sim-guava-{letter}' for letter in 'abcd'}


class SharedRawRuntime:
    def __init__(self, path, identity, references, *, profile_id=None, namespace=None):
        if profile_id not in (None, GUAVA_PROFILE_ID):
            raise ValueError('Guava requires its reviewed research-v1 RAW profile')
        self.path = Path(path).expanduser().absolute()
        self.identity = identity
        self.references = references
        self.explicit_namespace = namespace
        self.enabled = (profile_id is not None or namespace is not None
                        or os.environ.get('PUBLIC_MARKET_DATA_RAW') == '1')

    def namespace(self):
        runtime = self.identity['job_name']
        if runtime not in RESEARCH_JOBS:
            raise ValueError('Guava RAW owner must name an accountless research runtime')
        if self.explicit_namespace is not None:
            _namespace(self.explicit_namespace, 'golden-guava')
            namespace = self.explicit_namespace
        else:
            if (self.path.resolve() != self.path or self.path.name != 'trades_sim.db'
                    or self.path.parent.parent.name != 'data' or self.path.parent.name != runtime):
                raise ValueError('Guava RAW requires its canonical data/runtime path or an explicit namespace')
            namespace = scalar_namespace(os.environ.get('PUBLIC_MARKET_DATA_SOURCE'),
                os.environ.get('JOB_NAME'), 'golden-guava', runtime)
        owner = json.loads(namespace)
        if (owner['runtime'],owner['jenkins_job']) != (runtime,RESEARCH_JOBS[runtime]):
            raise ValueError('Guava RAW runtime/Jenkins job owner differs from research identity')
        return namespace

    def check(self, connection, *, write=False):
        metadata = raw_layout_metadata(connection)
        if metadata is None:
            if not write or not self.enabled:
                return None
            validate_raw_source_schema(connection,profile_id=GUAVA_PROFILE_ID)
            if any(connection.execute(f'SELECT 1 FROM main."{table}" LIMIT 1').fetchone()
                   for table in raw_profile(GUAVA_PROFILE_ID).tables):
                raise ValueError('populated Guava source requires explicit offline derivative migration')
            namespace = self.namespace()
            require_raw_capabilities(self.references.writer,profile_id=GUAVA_PROFILE_ID)
            if (self.references.reader is None or self.references.reader.scalar_authority_identity()
                    != self.references.writer.scalar_authority_identity()):
                raise ValueError('Guava RAW public reader/writer authorities differ')
            return namespace
        if metadata['profile_id'] != GUAVA_PROFILE_ID:
            raise ValueError('Guava RAW profile differs from research identity')
        validate_raw_source_schema(connection,profile_id=GUAVA_PROFILE_ID)
        owner = json.loads(metadata['namespace'])
        runtime = self.identity['job_name']
        if (owner['runtime'],owner['jenkins_job']) != (runtime,RESEARCH_JOBS.get(runtime)):
            raise ValueError('Guava RAW runtime/Jenkins job owner differs from research identity')
        if write or self.explicit_namespace is not None:
            if self.namespace() != metadata['namespace']:
                raise ValueError('Guava RAW source/job/runtime differs from bound owner')
        elif any(os.environ.get(key) is not None and os.environ[key] != owner[field]
                 for key,field in (('PUBLIC_MARKET_DATA_SOURCE','source'),('JOB_NAME','jenkins_job'))):
            raise ValueError('Guava RAW reader source/job differs from bound owner')
        refs = self.references
        if refs.reader is None or refs.reader.scalar_authority_identity() != metadata['authority_uuid']:
            raise ValueError('Guava RAW reader authority differs')
        if write:
            require_raw_capabilities(refs.writer,profile_id=GUAVA_PROFILE_ID)
            if refs.writer.scalar_authority_identity() != metadata['authority_uuid']:
                raise ValueError('Guava RAW writer authority differs')
        self.enabled = True
        return metadata['namespace']

    def initialize(self, connection):
        if self.enabled:
            self.check(connection,write=True)
            initialize_raw_links(connection,self.namespace(),self.references.writer.scalar_authority_identity(),
                                 references=self.references,profile_id=GUAVA_PROFILE_ID)
