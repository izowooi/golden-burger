"""Sibling DB files cannot overwrite or silently replace snapshot provenance."""
from pathlib import Path

from daily_rsync.snapshot_manifests import snapshot_manifest_path


def test_each_database_has_distinct_new_manifest_path(tmp_path):
    databases=[tmp_path/name for name in ('trades.db','trades_sim.db','shadow.db')]
    paths=[snapshot_manifest_path(p,for_write=True) for p in databases]
    assert len(set(paths))==3
    assert [p.name for p in paths]==['trades.db.manifest.json','trades_sim.db.manifest.json','shadow.db.manifest.json']


def test_legacy_is_read_only_fallback_and_scoped_manifest_has_priority(tmp_path):
    db=tmp_path/'shadow.db';legacy=tmp_path/'manifest.json'
    legacy.write_text('legacy')
    assert snapshot_manifest_path(db)==legacy
    scoped=snapshot_manifest_path(db,for_write=True);scoped.write_text('scoped')
    assert snapshot_manifest_path(db)==scoped
    assert legacy.read_text()=='legacy'


def test_dangling_scoped_symlink_never_falls_back_to_legacy(tmp_path):
    db=tmp_path/'shadow.db';legacy=tmp_path/'manifest.json'
    legacy.write_text('legacy')
    scoped=snapshot_manifest_path(db,for_write=True);scoped.symlink_to(tmp_path/'missing')
    assert snapshot_manifest_path(db)==scoped
