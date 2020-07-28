#!/bin/bash -xe

PROJECT=${PROJECT:-${PWD##*/}}
PROJECT_PATH="$PWD"
CONTAINER_WORKSPACE="/workspace/$PROJECT"
CONTAINER_IMAGE="${CONTAINER_IMAGE:=ovirt/$PROJECT-test-func-network-centos-8}"
CONTAINER_CMD=${CONTAINER_CMD:=podman}
VDSM_WORKDIR="/vdsm-tmp"
NMSTATE_WORKSPACE="/workspace/nmstate"
NMSTATE_TMP="/nmstate-tmp"

nmstate_mount=""

SWITCH_TYPE_LINUX="linux-bridge"
SWITCH_TYPE_OVS="ovs"

BACKEND_LEGACY="legacy"
BACKEND_NMSTATE="nmstate"

SWITCH_TYPE="${SWITCH_TYPE:=$SWITCH_TYPE_LINUX}"
BACKEND="${BACKEND:=$BACKEND_NMSTATE}"

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

function container_shell {
    ${CONTAINER_CMD} exec "-i$USE_TTY" "$CONTAINER_ID" /bin/bash
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
        cp $CONTAINER_WORKSPACE/static/etc/NetworkManager/conf.d/vdsm.conf /etc/NetworkManager/conf.d/
    "
    if [ $BACKEND == $BACKEND_LEGACY ];then
        container_exec "cp $CONTAINER_WORKSPACE/static/etc/dhcp/dhclient.d/dhclientmon.sh /etc/dhcp/dhclient.d/"
    elif [ $BACKEND == $BACKEND_NMSTATE ];then
        container_exec "cp $CONTAINER_WORKSPACE/static/etc/NetworkManager/dispatcher.d/dhcp_monitor.py etc/NetworkManager/dispatcher.d/"
    fi
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

function replace_resolvconf {
    container_exec "
        umount /etc/resolv.conf \
        && \
        echo -e 'nameserver 8.8.8.8\nnameserver 8.8.4.4' > /etc/resolv.conf
    "
}

function install_nmstate_from_source {
    container_exec "
        mkdir $NMSTATE_TMP \
        && \
        cp -rf $NMSTATE_WORKSPACE $NMSTATE_TMP/ \
        && \
        cd $NMSTATE_TMP/nmstate \
        && \
        pip3 install --no-deps -U . \
        && \
        cd -
    "
}

function clone_nmstate {
    container_exec "
        git clone --depth=50 https://github.com/nmstate/nmstate.git $NMSTATE_WORKSPACE \
        && \
        cd $NMSTATE_WORKSPACE \
        && \
        git fetch origin +refs/pull/$nmstate_pr/head: \
        && \
        git checkout -qf FETCH_HEAD \
        && \
        cd -
    "
}

function disable_nmstate {
    container_exec "
          mkdir /etc/vdsm && \
          echo -e \"[vars]\nnet_nmstate_enabled = false\n\" >> /etc/vdsm/vdsm.conf
    "
}

function enable_ipv6 {
    container_exec "echo 0 > /proc/sys/net/ipv6/conf/all/disable_ipv6"
}

options=$(getopt --options "" \
    --long help,shell,switch-type:,backend:,nmstate-pr:,nmstate-source:,pytest-args:\
    -- "${@}")
eval set -- "$options"
while true; do
    case "$1" in
    --shell)
        debug_shell="1"
        ;;
    --switch-type)
        shift
        SWITCH_TYPE="$1"
        ;;
    --backend)
        shift
        BACKEND="$1"
        ;;
    --nmstate-pr)
        shift
        nmstate_pr="$1"
        ;;
    --nmstate-source)
        shift
        nmstate_source="1"
        nmstate_mount="-v $1:$NMSTATE_WORKSPACE:Z"
        ;;
    --pytest-args)
        shift
        additional_pytest_args="$1"
        ;;
    --help)
        set +x
        echo -n "$0 [--shell] [--help] [--switch-type=<SWITCH_TYPE>] [--backend=<BACKEND>] [--nmstate-pr=<PR_ID>] "
        echo -n "[--nmstate-source=<PATH_TO_NMSTATE_SRC>] [--pytest-args=<ADDITIONAL_PYTEST_ARGUMENTS>]"
        echo "  Valid SWITCH_TYPEs are:"
        echo "     * $SWITCH_TYPE_LINUX (default)"
        echo "     * $SWITCH_TYPE_OVS"
        echo "  Valid BACKENDs are:"
        echo "     * $BACKEND_NMSTATE (default)"
        echo "     * $BACKEND_LEGACY"
        exit
        ;;
    --)
        shift
        break
        ;;
    esac
    shift
done

if [ -n "$CI" ]; then
    enable_bonding_driver
else
    load_kernel_modules
fi

CONTAINER_ID="$($CONTAINER_CMD run --privileged -d -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z $nmstate_mount --env PYTHONPATH=lib $CONTAINER_IMAGE)"
trap run_exit EXIT

wait_for_active_service "dbus"
start_service "systemd-udevd"
setup_vdsm_runtime_environment
restart_service "NetworkManager"
setup_vdsm_sources_for_testing

if [ -n "$nmstate_pr" ]; then
  clone_nmstate
  install_nmstate_from_source
fi

if [ -n "$nmstate_source" ]; then
  install_nmstate_from_source
fi

replace_resolvconf

if [ $SWITCH_TYPE == $SWITCH_TYPE_LINUX ];then
    SWITCH_TYPE="legacy_switch"
elif [ $SWITCH_TYPE == $SWITCH_TYPE_OVS ];then
    start_service "openvswitch"
    SWITCH_TYPE="ovs_switch"
    stable_link_skip="--skip-stable-link-monitor"
fi

if [ $BACKEND == $BACKEND_LEGACY ];then
    disable_nmstate
elif [ $BACKEND == $BACKEND_NMSTATE ];then
   SWITCH_TYPE="${SWITCH_TYPE} and nmstate"
fi

if [ "$TRAVIS" == "true" ]; then
    # Workaround for https://github.com/travis-ci/travis-ci/issues/8891
    enable_ipv6
fi

if [ -n "$debug_shell" ];then
    container_shell
    exit 0
fi

container_exec "
    cd /$VDSM_WORKDIR/$PROJECT \
    && \
    pytest \
      -vv \
      --target-lib \
      $stable_link_skip \
      -m \"$SWITCH_TYPE\" \
      tests/network/functional \
      $additional_pytest_args
"
