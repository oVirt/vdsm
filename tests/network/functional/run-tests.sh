#!/bin/bash -e

PROJECT=${PROJECT:-${PWD##*/}}
PROJECT_PATH="$PWD"
CONTAINER_WORKSPACE="/workspace/$PROJECT"
CONTAINER_IMAGE="${CONTAINER_IMAGE:=ovirt/$PROJECT-test-func-network-centos-8}"
VDSM_WORKDIR="/vdsm-tmp"

test -t 1 && USE_TTY="-t"

function run_exit {
    remove_container
}

function remove_container {
    res=$?
    [ "$res" -ne 0 ] && echo "*** ERROR: $res"
    podman rm -f "$CONTAINER_ID"
}

function podman_exec {
    podman exec "$USE_TTY" -i "$CONTAINER_ID" /bin/bash -c "$1"
}

function load_kernel_modules {
    modprobe 8021q
    modprobe bonding
    modprobe openvswitch
}

function wait_for_active_service {
    podman_exec "while ! systemctl is-active "$1"; do sleep 1; done"
}

function start_service {
    podman_exec "
        systemctl start '$1' && \
        while ! systemctl is-active '$1'; do sleep 1; done
    "
}

function restart_service {
    podman_exec "
        systemctl restart '$1' && \
        while ! systemctl is-active '$1'; do sleep 1; done
    "
}

function setup_vdsm_runtime_environment {
    podman_exec "
        adduser vdsm \
        && \
        install -d /var/run/vdsm/dhclientmon -m 755 -o vdsm && \
        install -d /var/run/vdsm/trackedInterfaces -m 755 -o vdsm && \
        cp $CONTAINER_WORKSPACE/static/etc/dhcp/dhclient.d/dhclientmon.sh /etc/dhcp/dhclient.d/ && \
        cp $CONTAINER_WORKSPACE/static/etc/NetworkManager/conf.d/vdsm.conf /etc/NetworkManager/conf.d/
    "
}

function setup_vdsm_sources_for_testing {
    podman_exec "
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

load_kernel_modules

CONTAINER_ID="$(podman run --privileged -d --dns=none -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z --env PYTHONPATH=lib $CONTAINER_IMAGE)"
trap run_exit EXIT

wait_for_active_service "dbus"
start_service "systemd-udevd"
setup_vdsm_runtime_environment
restart_service "NetworkManager"
setup_vdsm_sources_for_testing

if [ -n "$TEST_OVS" ];then
    SWITCH_TYPE="ovs_switch"
    start_service "openvswitch"
else
    SWITCH_TYPE="legacy_switch"
fi

if [ -n "$TEST_NMSTATE" ];then
  SWITCH_TYPE="${SWITCH_TYPE} and nmstate"
  podman_exec "
          mkdir /etc/vdsm && \
          echo -e \"[vars]\nnet_nmstate_enabled = true\n\" >> /etc/vdsm/vdsm.conf
  "
fi

if [ "$1" == "--shell" ];then
    podman_exec "bash"
    exit 0
fi

podman_exec "
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
