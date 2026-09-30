"""Mutable snapshot attestations belong to a database file, not its directory.

Pins and review generations each own a unique directory and retain their
manifest.json contract. Older mutable snapshots are read through the legacy
name only when no file-scoped attestation exists; identity checks remain with
the caller and must reject a sibling database's old manifest.
"""
import os
from pathlib import Path


def snapshot_manifest_path(database, *, for_write=False):
    database=Path(database)
    scoped=database.with_name(database.name+'.manifest.json')
    if for_write or os.path.lexists(scoped):return scoped
    return database.parent/'manifest.json'
