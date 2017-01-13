#!/bin/bash -ex
export LIBGUESTFS_BACKEND=direct
# ensure /dev/kvm exists, otherwise it will still use
# direct backend, but without KVM(much slower).
! [[ -c "/dev/kvm" ]] && mknod /dev/kvm c 10 232
AUTOMATION="$PWD"/automation
PREFIX="$AUTOMATION"/vdsm_functional
EXPORTS="$PWD"/exported-artifacts
TEST_PATH="functional"
FUNCTIONAL_TESTS_LIST=" \
    $TEST_PATH/supervdsmFuncTests.py \
    $TEST_PATH/upgrade_vdsm_test.py"

DISABLE_TESTS_LIST=" \
    $TEST_PATH/sosPluginTests.py \
    $TEST_PATH/vmRecoveryTests.py \
    $TEST_PATH/momTests.py \
    $TEST_PATH/networkTests.py \
    $TEST_PATH/vmQoSTests.py \
    $TEST_PATH/virtTests.py \
    $TEST_PATH/storageTests.py \
    $TEST_PATH/networkTestsOVS.py"

# Creates RPMS
"$AUTOMATION"/build-artifacts.sh

if [[ -d "$PREFIX" ]]; then
    pushd "$PREFIX"
    echo 'cleaning old lago env'
    lago cleanup || :
    popd
    rm -rf "$PREFIX"
fi

# Fix when running in an el* chroot in fc2* host
[[ -e /usr/bin/qemu-kvm ]] \
|| ln -s /usr/libexec/qemu-kvm /usr/bin/qemu-kvm


lago init \
    "$PREFIX" \
    "$AUTOMATION"/lago-env.yml
# If testing locally in the rh office you can use the option
# --template-repo-path=http://10.35.18.63/repo/repo.metadata

cd "$PREFIX"

# Make sure that there are no cached local repos, will not be needed once lago
# can handle local rpms properly
lago ovirt reposetup \
    --reposync-yum-config /dev/null \
    --custom-source "dir:$EXPORTS"

function mount_tmpfs {
    lago shell "$vm_name" -c "mount -t tmpfs tmpfs /sys/kernel/mm/ksm"
}

function run_functional_tests {
    local res=0
    lago shell "$vm_name" -c \
        " \
            cd /usr/share/vdsm/tests
            ./run_tests.sh \
                --with-xunit \
                --xunit-file=/tmp/nosetests-${distro}.xml \
                -s \
                $FUNCTIONAL_TESTS_LIST
        " || res=$?
    return $res
}

function run_network_tests {
    local res=0
    lago shell "$vm_name" -c \
        " \
            systemctl stop NetworkManager
            systemctl mask NetworkManager
            cd /usr/share/vdsm/tests
            ./run_tests.sh \
                -a type=functional,switch=legacy \
                network/func_*_test.py
        " || res=$?
    return $res
}

mkdir "$EXPORTS"/lago-logs
VMS_PREFIX="vdsm_functional_tests_host-"
failed=0
for distro in el7; do
    vm_name="${VMS_PREFIX}${distro}"
    # starting vms one by one to avoid exhausting memory in the host, it will
    lago start "$vm_name"
    lago copy-to-vm "$vm_name" /etc/yum/yum.conf /etc/yum/yum.conf
    # the ovirt deploy is needed because it will not start the local repo
    # otherwise
    lago ovirt deploy

    lago ovirt serve &
    PID=$!

    mount_tmpfs

    run_functional_tests | tee "$EXPORTS/functional_tests_stdout.$distro.log"
    failed="${PIPESTATUS[0]}"

    run_network_tests | tee -a "$EXPORTS/functional_tests_stdout.$distro.log"
    res="${PIPESTATUS[0]}"
    [ "$res" -ne 0 ] && failed="$res"

    kill $PID

    lago copy-from-vm \
        "$vm_name" \
        "/tmp/nosetests-${distro}.xml" \
        "$EXPORTS/nosetests-${distro}.xml" || :
    lago collect --output "$EXPORTS"/lago-logs
    lago stop "$vm_name"
done

lago cleanup

cp "$PREFIX"/current/logs/*.log "$EXPORTS"/lago-logs

exit $failed
