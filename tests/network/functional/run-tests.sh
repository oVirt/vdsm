#!/bin/bash -xe

PROJECT=${PROJECT:-${PWD##*/}}
PROJECT_PATH="$PWD"
CONTAINER_WORKSPACE="/workspace/$PROJECT"
CONTAINER_IMAGE="${CONTAINER_IMAGE:=ovirt/$PROJECT-test-func-network-centos-8}"
CONTAINER_CMD=${CONTAINER_CMD:=podman}
VDSM_WORKDIR="/vdsm-tmp"

test -t 1 && USE_TTY="t"

function run_exit {
    remove_container
}

function remove_container {
    res=$?
    [ "$res" -ne 0 ] && echo "*** ERROR: $res"
    ${CONTAINER_CMD} rm -f "$CONTAINER_ID"
}

function container_exec {
    ${CONTAINER_CMD} exec "-i$USE_TTY" "$CONTAINER_ID" /bin/bash -c "$1"
}

function load_kernel_modules {
    modprobe 8021q
    modprobe bonding
    modprobe openvswitch
}

function enable_bonding_driver {
    ip link add bond0000 type bond
    ip link delete bond0000
}

function wait_for_active_service {
    container_exec "while ! systemctl is-active "$1"; do sleep 1; done"
}

function start_service {
    container_exec "
        systemctl start '$1' && \
        while ! systemctl is-active '$1'; do sleep 1; done
    "
}

function restart_service {
    container_exec "
        systemctl restart '$1' && \
        while ! systemctl is-active '$1'; do sleep 1; done
    "
}

function setup_vdsm_runtime_environment {
    container_exec "
        adduser vdsm \
        && \
        install -d /var/run/vdsm/dhclientmon -m 755 -o vdsm && \
        install -d /var/run/vdsm/trackedInterfaces -m 755 -o vdsm && \
        cp $CONTAINER_WORKSPACE/static/etc/dhcp/dhclient.d/dhclientmon.sh /etc/dhcp/dhclient.d/ && \
        cp $CONTAINER_WORKSPACE/static/etc/NetworkManager/conf.d/vdsm.conf /etc/NetworkManager/conf.d/
    "
}

function setup_vdsm_sources_for_testing {
    container_exec "
        mkdir $VDSM_WORKDIR \
        && \
        cp -rf $CONTAINER_WORKSPACE $VDSM_WORKDIR/ \
        && \
        cd /$VDSM_WORKDIR/$PROJECT \
        && \
        git clean -dxf \
        &&
        ./autogen.sh --system \
        && \
        make
    "
}

if [ -n "$CI" ]; then
    enable_bonding_driver
else
    load_kernel_modules
fi

CONTAINER_ID="$($CONTAINER_CMD run --privileged -d --dns=8.8.8.8 --dns=8.8.4.4 -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z --env PYTHONPATH=lib $CONTAINER_IMAGE)"
trap run_exit EXIT

wait_for_active_service "dbus"
start_service "systemd-udevd"
setup_vdsm_runtime_environment
restart_service "NetworkManager"
setup_vdsm_sources_for_testing

if [ -n "$TEST_OVS" ];then
    SWITCH_TYPE="ovs_switch"
    start_service "openvswitch"
    container_exec "umount /etc/resolv.conf"
else
    SWITCH_TYPE="legacy_switch"
fi

if [ -n "$TEST_NMSTATE" ];then
  SWITCH_TYPE="${SWITCH_TYPE} and nmstate"
  container_exec "
          mkdir /etc/vdsm && \
          echo -e \"[vars]\nnet_nmstate_enabled = true\n\" >> /etc/vdsm/vdsm.conf
  "
fi

if [ "$1" == "--shell" ];then
    container_exec "bash"
    exit 0
fi

container_exec "
    cd /$VDSM_WORKDIR/$PROJECT \
    && \
    pytest \
      -vv \
      --log-level=DEBUG \
      --target-lib \
      --skip-stable-link-monitor \
      -m \"$SWITCH_TYPE\" \
      tests/network/functional
"
