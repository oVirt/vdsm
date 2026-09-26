#!/bin/bash
# NVMe-oF/TCP Integration Test Script
#
# Usage:
#   sudo ./nvmeof_integration_test.sh setup     - Create local nvmet target + connect
#   sudo ./nvmeof_integration_test.sh teardown   - Disconnect + remove nvmet target
#   sudo ./nvmeof_integration_test.sh test       - Full test cycle
#
# Prerequisites:
#   - kernel >= 5.14 with nvmet, nvmet-tcp, nvme-tcp modules
#   - nvme-cli >= 2.2
#   - device-mapper-multipath
#   - VDSM installed (for VDSM-specific tests)

set -euo pipefail

NQN="nqn.2026-06.org.ovirt:nvmeof-test"
TRADDR="127.0.0.1"
TRSVCID="4420"
TRANSPORT="tcp"
NULL_BLK_SIZE_MB=1024
SUBSYS_DIR="/sys/kernel/config/nvmet/subsystems/${NQN}"
PORT_DIR="/sys/kernel/config/nvmet/ports/1"

log() {
    echo "[$(date +%H:%M:%S)] $*"
}

check_prereqs() {
    log "Checking prerequisites..."
    local missing=0

    if ! command -v nvme &>/dev/null; then
        echo "ERROR: nvme-cli not found. Install with: dnf install -y nvme-cli"
        missing=1
    fi

    if ! lsmod | grep -q nvme_tcp && ! modprobe nvme-tcp 2>/dev/null; then
        echo "ERROR: nvme-tcp module not available"
        missing=1
    fi

    if ! lsmod | grep -q nvmet && ! modprobe nvmet 2>/dev/null; then
        echo "ERROR: nvmet module not available"
        missing=1
    fi

    if ! lsmod | grep -q nvmet_tcp && ! modprobe nvmet-tcp 2>/dev/null; then
        echo "ERROR: nvmet-tcp module not available"
        missing=1
    fi

    if [ "$missing" -eq 1 ]; then
        exit 1
    fi
    log "All prerequisites met"
}

setup_target() {
    log "Setting up nvmet target..."

    # Mount configfs if not already mounted
    if ! mountpoint -q /sys/kernel/config; then
        mount -t configfs none /sys/kernel/config
    fi

    # Clean up any previous setup
    teardown_target 2>/dev/null || true

    # Create subsystem
    mkdir -p "$SUBSYS_DIR"
    echo 1 > "${SUBSYS_DIR}/attr_allow_any_host"
    log "  Subsystem ${NQN} created"

    # Load null_blk for backing store
    if ! lsmod | grep -q null_blk; then
        modprobe null_blk nr_devices=1 size=$((NULL_BLK_SIZE_MB * 2))
        log "  null_blk device loaded (${NULL_BLK_SIZE_MB}M)"
    fi

    # Find the null_blk device
    local nullb
    nullb=$(lsblk -nlo NAME | grep nullb | head -1)
    if [ -z "$nullb" ]; then
        echo "ERROR: no null_blk device found"
        exit 1
    fi
    log "  Using backing device: /dev/${nullb}"

    # Create namespace
    mkdir -p "${SUBSYS_DIR}/namespaces/10"
    echo -n "/dev/${nullb}" > "${SUBSYS_DIR}/namespaces/10/device_path"
    echo 1 > "${SUBSYS_DIR}/namespaces/10/enable"
    log "  Namespace 10 created"

    # Create TCP port
    mkdir -p "$PORT_DIR"
    echo "$TRADDR" > "${PORT_DIR}/addr_traddr"
    echo "$TRANSPORT" > "${PORT_DIR}/addr_trtype"
    echo "$TRSVCID" > "${PORT_DIR}/addr_trsvcid"

    # Link subsystem to port
    ln -s "$SUBSYS_DIR" "${PORT_DIR}/subsystems/${NQN}"
    log "  TCP port ${TRADDR}:${TRSVCID} enabled"
    log "Target ready"
}

teardown_target() {
    log "Tearing down nvmet target..."

    # Disconnect any initiators
    nvme disconnect -n "$NQN" 2>/dev/null || true

    # Remove port subsystem symlink
    if [ -L "${PORT_DIR}/subsystems/${NQN}" ]; then
        rm -f "${PORT_DIR}/subsystems/${NQN}"
    fi

    # Remove port
    if [ -d "$PORT_DIR" ]; then
        rmdir "$PORT_DIR" 2>/dev/null || true
    fi

    # Remove subsystem
    if [ -d "$SUBSYS_DIR" ]; then
        rmdir "$SUBSYS_DIR" 2>/dev/null || true
    fi

    # Remove null_blk
    if lsmod | grep -q null_blk; then
        rmmod null_blk 2>/dev/null || true
    fi

    log "Target torn down"
}

test_connect() {
    log "Testing NVMe-oF connect..."
    nvme connect -n "$NQN" -t "$TRANSPORT" -a "$TRADDR" -s "$TRSVCID"
    log "Connect successful"

    # Give udev time to settle
    udevadm settle
    sleep 1
}

test_disconnect() {
    log "Testing NVMe-oF disconnect..."
    nvme disconnect -n "$NQN"
    log "Disconnect successful"
}

test_nvme_list() {
    log "Verifying nvme list..."
    nvme list | grep -q "$NQN" || {
        echo "FAIL: NQN not found in nvme list output"
        nvme list
        exit 1
    }
    log "  Device visible in nvme list"
}

test_multipath() {
    log "Testing multipath detection..."
    local mpath_count
    mpath_count=$(multipath -ll 2>/dev/null | grep -c "${NQN}" || true)
    if [ "$mpath_count" -gt 0 ]; then
        log "  Multipath device(s) detected: ${mpath_count}"
    else
        log "  WARNING: No multipath device detected (may need multipath -r)"
        multipath -r 2>/dev/null || true
        sleep 1
    fi
}

test_vdsm_devicelist() {
    log "Testing VDSM getDeviceList..."
    if python3 -c "import vdsm.storage.hsm" 2>/dev/null; then
        python3 -c "
from vdsm.storage import hsm
h = hsm.HSM()
result = h.getDeviceList(storageType=12, guids=[], checkStatus=False)
if result['status']['code'] != 0:
    print('FAIL: getDeviceList error:', result['status'])
    exit(1)
devices = result.get('devlist', [])
nvmeof_devices = [d for d in devices if d.get('devtype') == 'NVMe-oF']
if nvmeof_devices:
    print('  NVMe-oF devices found:', len(nvmeof_devices))
    for d in nvmeof_devices:
        print('    -', d.get('name'), d.get('guid'))
else:
    print('  WARNING: No NVMe-oF devices in getDeviceList')
    print('  All devices:', [d.get('name', '?') + ':' + d.get('devtype', '?') for d in devices])
" 2>&1
    else
        log "  SKIP: VDSM not importable (not running on VDSM host)"
    fi
}

test_engine_api() {
    local engine_url="${ENGINE_URL:-}"
    local engine_user="${ENGINE_USER:-admin@internal}"
    local engine_password="${ENGINE_PASSWORD:-}"

    if [ -z "$engine_url" ] || [ -z "$engine_password" ]; then
        log "  SKIP: ENGINE_URL and ENGINE_PASSWORD environment variables not set"
        return
    fi

    log "Testing Engine REST API..."
    local host_id
    host_id=$(curl -sk -u "${engine_user}:${engine_password}" \
        "${engine_url}/ovirt-engine/api/hosts" \
        | sed -n 's/.*href="\([^"]*\)".*/\1/p' | head -1 | xargs basename)

    log "  Host ID: ${host_id}"

    # Create storage connection
    local conn_response
    conn_response=$(curl -sk -X POST -u "${engine_user}:${engine_password}" \
        -H "Content-Type: application/xml" \
        -d "<storage_connection>
                <type>nvmeof</type>
                <address>${TRADDR}</address>
                <nqn>${NQN}</nqn>
                <port>${TRSVCID}</port>
             </storage_connection>" \
        "${engine_url}/ovirt-engine/api/storageconnections")
    local conn_id
    conn_id=$(echo "$conn_response" | sed -n 's|.*<id>\([^<]*\)</id>.*|\1|p')
    log "  Connection ID: ${conn_id}"

    # Delete connection
    curl -sk -X DELETE -u "${engine_user}:${engine_password}" \
        "${engine_url}/ovirt-engine/api/storageconnections/${conn_id}"

    log "Engine REST API test passed"
}

test_all() {
    log "=== NVMe-oF/TCP Integration Test ==="
    check_prereqs
    setup_target
    test_connect
    test_nvme_list
    test_multipath
    test_vdsm_devicelist
    test_disconnect
    teardown_target
    log "=== All tests passed ==="
}

case "${1:-help}" in
    setup)
        check_prereqs
        setup_target
        test_connect
        test_nvme_list
        log "Setup complete. Run 'sudo $0 teardown' to clean up."
        ;;
    teardown)
        teardown_target
        log "Cleanup complete"
        ;;
    test)
        test_all
        ;;
    engine-test)
        test_engine_api
        ;;
    *)
        echo "Usage: $0 {setup|teardown|test|engine-test}"
        echo ""
        echo "  setup       - Create local nvmet target and connect"
        echo "  teardown    - Disconnect and remove nvmet target"
        echo "  test        - Run full integration test suite"
        echo "  engine-test - Test Engine REST API (set ENGINE_URL, ENGINE_PASSWORD)"
        exit 1
        ;;
esac
