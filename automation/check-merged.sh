#!/bin/bash -ex

export LIBGUESTFS_BACKEND=direct

# ensure /dev/kvm exists, otherwise it will still use
# direct backend, but without KVM(much slower).
! [[ -c "/dev/kvm" ]] && mknod /dev/kvm c 10 232


DISTRO='el7'
VM_NAME="vdsm_functional_tests_host-${DISTRO}"
AUTOMATION="$PWD"/automation
PREFIX="$AUTOMATION"/vdsm_functional
EXPORTS="$PWD"/exported-artifacts

function prepare {
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

    cd "$PREFIX"
    lago ovirt reposetup \
        --custom-source "dir:$EXPORTS"
}

function fake_ksm_in_vm {
    lago shell "$VM_NAME" -c "mount -t tmpfs tmpfs /sys/kernel/mm/ksm"
}

function run_infra_tests {
    local res=0
    lago shell "$VM_NAME" -c \
        " \
            cd /usr/share/vdsm/tests
            ./run_tests.sh \
                --with-xunit \
                --xunit-file=/tmp/nosetests-${DISTRO}.xml \
                -s \
                functional/supervdsmFuncTests.py \
                functional/upgrade_vdsm_test.py \
        " || res=$?
    return $res
}

function run_network_tests {
    local res=0
    lago shell "$VM_NAME" -c \
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

function prepare_and_copy_yum_conf {
    local tempfile=$(mktemp XXXXXX)

    cat /etc/yum/yum.conf 2>/dev/null | \
    grep -v "reposdir" | \
    "$AUTOMATION"/exclude_from_conf 'vdsm*' > "$tempfile"

    lago copy-to-vm "$VM_NAME" "$tempfile" /etc/yum/yum.conf
    rm "$tempfile"
}

function run {
    mkdir "$EXPORTS"/lago-logs
    failed=0

    lago start "$VM_NAME"

    prepare_and_copy_yum_conf

    # the ovirt deploy is needed because it will not start the local repo
    # otherwise
    lago ovirt deploy

    lago ovirt serve &
    PID=$!

    fake_ksm_in_vm

    run_infra_tests | tee "$EXPORTS/functional_tests_stdout.$DISTRO.log"
    failed="${PIPESTATUS[0]}"

    run_network_tests | tee -a "$EXPORTS/functional_tests_stdout.$DISTRO.log"
    res="${PIPESTATUS[0]}"
    [ "$res" -ne 0 ] && failed="$res"

    kill $PID

    lago copy-from-vm \
    "$VM_NAME" \
    "/tmp/nosetests-${DISTRO}.xml" \
    "$EXPORTS/nosetests-${DISTRO}.xml" || :
    lago collect --output "$EXPORTS"/lago-logs

    cp "$PREFIX"/current/logs/*.log "$EXPORTS"/lago-logs
    return $failed
}

function cleanup {
    lago stop "$VM_NAME"
    lago cleanup
}

prepare && run && cleanup
exit $?
