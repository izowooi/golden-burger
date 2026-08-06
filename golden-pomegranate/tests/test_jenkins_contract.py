"""Static safety checks for the accountless Jenkins pipeline."""

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JENKINSFILE = PROJECT_ROOT / "Jenkinsfile"


def _source() -> str:
    return JENKINSFILE.read_text(encoding="utf-8")


def test_jenkins_uses_single_writer_external_workspace_and_capacity_cadence():
    source = _source()

    assert "disableConcurrentBuilds()" in source
    assert "timeout(time: 20, unit: 'MINUTES')" in source
    assert "buildDiscarder(logRotator(daysToKeepStr: '120'))" in source
    assert "cron('H/15 * * * *')" in source
    assert "POLYBOT_CADENCE_MINUTES = '15'" in source
    assert "cron('H/10 * * * *')" not in source
    assert "POMEGRANATE_MOUNT_ROOT = '/Volumes/t7'" in source
    assert 'ws("${env.POMEGRANATE_MOUNT_ROOT}/jenkins/golden-pomegranate")' in source
    assert (
        'EXPECTED_WORKSPACE="${POMEGRANATE_MOUNT_ROOT}/jenkins/golden-pomegranate"'
        in source
    )
    assert 'workspace / ".daily-rsync-workspace.json"' in source
    assert '"schema_version": 1' in source
    assert "UV_LINK_MODE = 'copy'" in source
    assert "POLYBOT_LIFECYCLE_MODE = 'archive_only'" in source
    assert "POMEGRANATE_RUNTIME_JOB = 'pomegranate-15m-v2'" in source
    assert 'RUNTIME_JOB="${POMEGRANATE_RUNTIME_JOB}"' in source


def test_jenkins_verifies_external_mount_off_volume_pin_and_workspace_device():
    source = _source()

    assert "${POMEGRANATE_MOUNT_ROOT}/.golden-pomegranate-volume" in source
    assert (
        "POMEGRANATE_HOST_UUID_PIN = "
        "'/Users/jongwoopark/.jenkins/golden-pomegranate-volume.uuid'"
    ) in source
    assert 'HOST_UUID_PIN="${POMEGRANATE_HOST_UUID_PIN}"' in source
    assert "File System Personality" in source
    assert "Mount Point" in source
    assert "Device Location" in source
    assert "Volume UUID" in source
    assert "golden-pomegranate-apfs-v1" in source
    assert '"${CURRENT_UUID}" != "${HOST_UUID}"' in source
    assert '"${SENTINEL_UUID}" != "${HOST_UUID}"' in source
    assert "MOUNT_DEVICE_ID" in source
    assert "WORKSPACE_DEVICE_ID" in source
    assert "HOST_PIN_DEVICE_ID" in source
    assert '"${HOST_PIN_DEVICE_ID}" = "${MOUNT_DEVICE_ID}"' in source
    assert '"${WORKSPACE_DEVICE_ID}" != "${MOUNT_DEVICE_ID}"' in source
    assert '/usr/sbin/diskutil info "${WORKSPACE}"' not in source


def test_jenkins_rejects_symlink_or_noncanonical_workspace():
    source = _source()

    assert '[ ! -d "${WORKSPACE}" ] || [ -L "${WORKSPACE}" ]' in source
    assert 'CANONICAL_WORKSPACE="$(cd "${WORKSPACE}" && /bin/pwd -P)"' in source
    assert '"${CANONICAL_WORKSPACE}" != "${EXPECTED_WORKSPACE}"' in source


def test_daily_rsync_workspace_marker_is_job_bound_post_checkout_and_atomic():
    source = _source()

    checkout_position = source.index("checkout scm")
    marker_position = source.index('target = workspace / ".daily-rsync-workspace.json"')
    collector_position = source.index("dir('golden-pomegranate')")
    assert checkout_position < marker_position < collector_position
    assert '"schema_version": 1' in source
    assert '"job": job' in source
    assert '"workspace": str(workspace)' in source
    assert ".daily-rsync-workspace.json.tmp.{os.getpid()}" in source
    assert "os.O_EXCL" in source
    assert "os.O_NOFOLLOW" in source
    assert "os.fsync(handle.fileno())" in source
    assert "os.replace(temporary, target)" in source
    assert "os.fsync(directory)" in source


def test_jenkins_is_simulation_only_and_runs_all_operator_checks_in_order():
    source = _source()
    commands = [
        "polybot config --simulate",
        "polybot health --simulate",
        "polybot run --simulate",
        "polybot status --simulate",
    ]

    positions = [source.index(command) for command in commands]
    assert positions == sorted(positions)
    assert source.count("polybot health --simulate") == 2
    assert "polybot run --live" not in source
    assert "sync --frozen" in source


def test_jenkins_has_no_secret_binding_or_inline_secret_assignment():
    source = _source()

    assert "withCredentials" not in source
    assert "credentials(" not in source
    assert "set -x" not in source
    assert source.count("set +x") == 4
    assert "POLYMARKET_PRIVATE_KEY+x" in source
    assert "POLYMARKET_FUNDER_ADDRESS+x" in source
    assert "POLYMARKET_SIGNATURE_TYPE+x" in source

    inline_secret = re.compile(
        r"(?:export\s+)?(?:POLYMARKET_PRIVATE_KEY|POLYMARKET_FUNDER_ADDRESS|"
        r"POLYMARKET_API_KEY|POLYMARKET_API_SECRET|CLOB_API_KEY|CLOB_SECRET)="
        r"[^\"'$\s]"
    )
    assert inline_secret.search(source) is None
