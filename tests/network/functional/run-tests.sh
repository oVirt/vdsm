#!/bin/bash -xe

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

source tests/network/common.sh

IMAGE_TAG="${IMAGE_TAG:=centos-8}"
CONTAINER_IMAGE="${CONTAINER_IMAGE:=$IMAGE_PREFIX-functional}"
NMSTATE_WORKSPACE="/workspace/nmstate"
NMSTATE_TMP="/nmstate-tmp"

nmstate_mount=""

SWITCH_TYPE_LINUX="linux-bridge"
SWITCH_TYPE_OVS="ovs"

SWITCH_TYPE="${SWITCH_TYPE:=$SWITCH_TYPE_LINUX}"

function setup_vdsm_runtime_environment {
    container_exec "
        adduser vdsm \
        && \
        install -d /run/vdsm -m 755 -o vdsm && \
        cp $CONTAINER_WORKSPACE/static/etc/NetworkManager/conf.d/vdsm.conf /etc/NetworkManager/conf.d/
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
    BUILD_NMSTATE = "make rpm"
    if grep -q "8" <<< $IMAGE_TAG ; then
      BUILD_NMSTATE = "pip3 install --no-deps -U ."
    fi
    container_exec "
        mkdir $NMSTATE_TMP \
        && \
        cp -rf $NMSTATE_WORKSPACE $NMSTATE_TMP/ \
        && \
        cd $NMSTATE_TMP/nmstate \
        && \
        $BUILD_NMSTATE \
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

function enable_ipv6 {
    container_exec "echo 0 > /proc/sys/net/ipv6/conf/all/disable_ipv6"
}

options=$(getopt --options "" \
    --long help,shell,switch-type:,nmstate-pr:,nmstate-source:,pytest-args:\
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
        echo -n "$0 [--shell] [--help] [--switch-type=<SWITCH_TYPE>] [--nmstate-pr=<PR_ID>] "
        echo -n "[--nmstate-source=<PATH_TO_NMSTATE_SRC>] [--pytest-args=<ADDITIONAL_PYTEST_ARGUMENTS>]"
        echo "  Valid SWITCH_TYPEs are:"
        echo "     * $SWITCH_TYPE_LINUX (default)"
        echo "     * $SWITCH_TYPE_OVS"
        exit
        ;;
    --)
        shift
        break
        ;;
    esac
    shift
done

load_kernel_modules

CONTAINER_ID="$($CONTAINER_CMD run --privileged -d -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z $nmstate_mount --env PYTHONPATH=lib --env CI $CONTAINER_IMAGE:$IMAGE_TAG)"
trap remove_container EXIT

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
    restart_service "NetworkManager"
    SWITCH_TYPE="ovs_switch"
fi

if [ "$TRAVIS" == "true" ]; then
    # Workaround for https://github.com/travis-ci/travis-ci/issues/8891
    enable_ipv6
fi

if [ -n "$debug_shell" ];then
    container_shell
    exit 0
fi

run_tests functional --target-lib -m "\"$SWITCH_TYPE\"" "$additional_pytest_args"
