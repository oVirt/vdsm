#!/bin/bash -xe

function init() {
    readonly TEST_RUN_TIMEOUT=3600
    export LIBGUESTFS_BACKEND=direct

    # ensure /dev/kvm exists, otherwise it will still use
    # direct backend, but without KVM(much slower).
    ! [[ -c "/dev/kvm" ]] && mknod /dev/kvm c 10 232

    # The following defines softlink for qemu-kvm which is required for jobs
    # running over fc2* hosts and chroot to el*
    [[ -e /usr/bin/qemu-kvm ]] \
    || ln -s /usr/libexec/qemu-kvm /usr/bin/qemu-kvm

    # ENV vars
    DISTRO='el7'
    VM_NAME="vdsm_functional_tests_host-${DISTRO}"
    AUTOMATION="$PWD"/automation
    PREFIX="$AUTOMATION"/vdsm_functional
    EXPORTS="$PWD"/exported-artifacts
    readonly TESTS_OUT="/root/vdsm-tests"
}

function setup_env {
    # TODO: jenkins mock env should take care of that
    if [[ -d "$PREFIX" ]]; then
        pushd "$PREFIX"
        echo 'cleaning old lago env'
        lago cleanup || :
        popd
        rm -rf "$PREFIX"
    fi

    # Creates RPMS
    "$AUTOMATION"/build-artifacts.sh

    lago init \
        "$PREFIX" \
        "$AUTOMATION"/lago-env.yml

    cd "$PREFIX"

    lago ovirt reposetup \
        --custom-source "dir:$EXPORTS"

    lago start "$VM_NAME"
    # the ovirt deploy is needed because it will not start the local repo
    # otherwise
    prepare_and_copy_yum_conf
    lago ovirt deploy
    lago shell "$VM_NAME" -c "mkdir -p /root/vdsm-tests"
}

function fake_ksm_in_vm {
    lago shell "$VM_NAME" -c "mount -t tmpfs tmpfs /sys/kernel/mm/ksm"
}

function install_test_dependencies {
    local res=0
    lago shell "$VM_NAME" -c \
        " \
            pip install -U \
                pytest==3.1.2 \
                pytest-forked==0.2 \
                xunitmerge==1.0.4
        " || res=$?
    return $res
}

function run_functional_network_test_linux_bridge {
    local res=0
    timeout $TEST_RUN_TIMEOUT lago shell "$VM_NAME" -c \
        " \
            cd /usr/share/vdsm/tests
            pytest \
                --junitxml=$TESTS_OUT/tests-${DISTRO}-network-legacy.junit.xml \
                -m legacy_switch \
                network/functional
        " || res=$?
    return $res
}

function run_functional_network_test_ovs_switch {
    local res=0
    timeout $TEST_RUN_TIMEOUT lago shell "$VM_NAME" -c \
        " \
            cd /usr/share/vdsm/tests
            systemctl start openvswitch
            pytest \
                --junitxml=$TESTS_OUT/tests-${DISTRO}-network-ovs.junit.xml \
                -m ovs_switch \
                network/functional
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

function run_test {
    local res=0
    local test_name="$1"

    prepare_test_dependencies

    $test_name | tee -a "$EXPORTS/${test_name}_stdout.$DISTRO.log"
    local net_ret="${PIPESTATUS[0]}"
    [ "$net_ret" -ne 0 ] && res="$net_ret"

    return $res
}

function prepare_test_dependencies {
    local res=0

    install_test_dependencies | tee "$EXPORTS/functional_tests_dependencies_stdout.$DISTRO.log"
    local dependencies_ret="${PIPESTATUS[0]}"
    [ "$dependencies_ret" -ne 0 ] && return "$dependencies_ret"

    return $res
}

function collect_logs {
    mkdir "$EXPORTS"/test_logs
    lago collect --output "$EXPORTS"/test_logs
    cp "$PREFIX"/current/logs/*.log "$EXPORTS"/test_logs/
}

function cleanup {
    lago stop "$VM_NAME"
    lago cleanup
}

function collect_and_clean {
    collect_logs
    cleanup
}