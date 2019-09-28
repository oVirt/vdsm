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
    DISTRO="$1"
    VM_NAME="vdsm_functional_tests_host-${DISTRO}"
    AUTOMATION="$PWD"/automation
    PREFIX="$AUTOMATION"/vdsm_functional
    EXPORTS="$PWD"/exported-artifacts
    SOURCES="$PWD"
    VDSM_LIB="/usr/share/vdsm"
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
    "$AUTOMATION"/build.sh

    if [[ $DISTRO == "fc29" ]]; then
        mkdir -p /tmp
        cp -r "$SOURCES" /tmp
    fi

    lago init \
        "$PREFIX" \
        "$AUTOMATION"/lago-env_"${DISTRO}".yml

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
            ${CI_PYTHON} -m pip install -U \
            pytest==4.2.1 \
            pytest-forked==0.2 \
            xunitmerge==1.0.4
        " || res=$?
    if [[ $res -eq 0 && $DISTRO == "fc29" ]]; then
        lago shell "$VM_NAME" -c \
            " \
                dnf install \
                dnsmasq \
                gcc \
                libvirt-devel \
                network-scripts \
                openvswitch \
                pkgconf-pkg-config \
                redhat-rpm-config \
                python3-dateutil \
                python3-devel \
                python3-inotify \
                python3-libvirt \
                python3-netaddr \
                python3-nose \
                python3-pyyaml \
                -y \
            " || res=$?
        copy_sources_to_vm
        set_up_vdsm_user_and_groups
        set_up_bonding_defaults_opts
    fi

    return $res
}

function copy_sources_to_vm {
    lago shell "$VM_NAME" -c "mkdir ${VDSM_LIB}"
    lago copy-to-vm "$VM_NAME" /tmp/vdsm /usr/share
}

function set_up_vdsm_user_and_groups {
    export vdsm_user=vdsm
    export vdsm_group=kvm
    export qemu_group=qemu
    export snlk_group=sanlock
    export cdrom_group=cdrom
    export qemu_user=qemu

    lago shell "$VM_NAME" -c \
        " \
        export PYTHONPATH='${VDSM_LIB}/lib:$PYTHONPATH'
        adduser vdsm
        adduser qemu

        groupadd sanlock
        groupadd qemu
        groupadd cdrom

        /usr/bin/getent passwd "${vdsm_user}" >/dev/null || /usr/sbin/useradd -r -u 36 -g "${vdsm_group}" -d /var/lib/vdsm -s /sbin/nologin -c 'Node Virtualization Manager' "${vdsm_user}"
        /usr/sbin/usermod -a -G "${qemu_group}","${snlk_group}" "${vdsm_user}"
        /usr/sbin/usermod -a -G "${cdrom_group}" "${qemu_user}"

        install -d /var/run/vdsm/dhclientmon -m 755 -o vdsm -g kvm
        install -d /var/run/vdsm/trackedInterfaces -m 755 -o vdsm -g kvm
        "
}

function set_up_bonding_defaults_opts {
    lago shell "$VM_NAME" -c \
        "
        export PYTHONPATH='${VDSM_LIB}/lib:$PYTHONPATH'

        modprobe bonding
        mkdir /var/run/vdsm

        cd ${VDSM_LIB}/tests
        python3 <<< cat <<EOF
from vdsm.network.link.bond import sysfs_options_mapper
sysfs_options_mapper.dump_bonding_options()
EOF
        "
}

function run_functional_network_test_linux_bridge {
    local res=0
    timeout $TEST_RUN_TIMEOUT lago shell "$VM_NAME" -c \
        " \
            cd ${VDSM_LIB}/tests
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
            cd ${VDSM_LIB}/tests
            systemctl start openvswitch
            pytest \
                --junitxml=$TESTS_OUT/tests-${DISTRO}-network-ovs.junit.xml \
                -m ovs_switch \
                network/functional
        " || res=$?
    return $res
}

function run_functional_network_test_linux_bridge_lib {
    local res=0
    timeout $TEST_RUN_TIMEOUT lago shell "$VM_NAME" -c \
        " \
            export PYTHONPATH='${VDSM_LIB}/lib:$PYTHONPATH'
            cd ${VDSM_LIB}/tests
            pytest \
                --junitxml=$TESTS_OUT/tests-${DISTRO}-network-legacy.junit.xml \
                --log-level=DEBUG \
                --target-lib \
                -m legacy_switch \
                network/functional
        " || res=$?
    return $res
}

function run_functional_network_test_ovs_switch_lib {
    local res=0
    timeout $TEST_RUN_TIMEOUT lago shell "$VM_NAME" -c \
        " \
            export PYTHONPATH='${VDSM_LIB}/lib:$PYTHONPATH'
            cd ${VDSM_LIB}/tests
            systemctl start openvswitch
            pytest \
                --junitxml=$TESTS_OUT/tests-${DISTRO}-network-ovs.junit.xml \
                --log-level=DEBUG \
                --target-lib \
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
