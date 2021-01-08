#!/bin/bash -xe

PROJECT=${PROJECT:-${PWD##*/}}
PROJECT_PATH="$PWD"
CONTAINER_WORKSPACE="/workspace/$PROJECT"
CONTAINER_CMD=${CONTAINER_CMD:=podman}
VDSM_WORKDIR="/vdsm-tmp"

test -t 1 && USE_TTY="t"

function container_exec {
    ${CONTAINER_CMD} exec "-i$USE_TTY" "$CONTAINER_ID" /bin/bash -c "$1"
}

function container_shell {
    ${CONTAINER_CMD} exec "-i$USE_TTY" "$CONTAINER_ID" /bin/bash
}

function remove_container {
    res=$?
    [ "$res" -ne 0 ] && echo "*** ERROR: $res"
    ${CONTAINER_CMD} rm -f "$CONTAINER_ID"
}

function load_kernel_modules {
    modprobe bonding
    modprobe openvswitch
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

function setup_vdsm_sources_for_testing {
    copy_sources_to_workdir
    container_exec "
        cd /$VDSM_WORKDIR/$PROJECT \
        && \
        git clean -dxf \
        && \
        ./autogen.sh --system \
        && \
        make
    "
}

function copy_sources_to_workdir {
    container_exec "
        mkdir $VDSM_WORKDIR \
        && \
        cp -rf $CONTAINER_WORKSPACE $VDSM_WORKDIR/
    "
}

function run_tests {
    container_exec "
        cd $VDSM_WORKDIR/$PROJECT \
        && \
        pytest \
          -vv \
          tests/network/$1 \
          ${*:2}
    "
}
