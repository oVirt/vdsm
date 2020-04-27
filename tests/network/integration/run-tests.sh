#!/bin/bash -xe

PROJECT=${PROJECT:-${PWD##*/}}
PROJECT_PATH="$PWD"
CONTAINER_WORKSPACE="/workspace/$PROJECT"
CONTAINER_IMAGE="${CONTAINER_IMAGE:=ovirt/$PROJECT-test-integration-network-centos-8}"
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

function copy_sources_to_workdir {
    container_exec "
        mkdir $VDSM_WORKDIR \
        && \
        cp -rf $CONTAINER_WORKSPACE $VDSM_WORKDIR/ \
        && \
        cd $VDSM_WORKDIR/$PROJECT \
        && \
        git clean -dxf \
        && \
        ./autogen.sh --system \
        && \
        make"
}

function run_integration_tests {
    container_exec "
        cd $VDSM_WORKDIR/$PROJECT \
        && \
        pytest -vv --log-level=DEBUG tests/network/integration
    "
}

function prepare_environment {
    modprobe openvswitch
}

CONTAINER_ID="$($CONTAINER_CMD run --privileged -d -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z --env PYTHONPATH=lib $CONTAINER_IMAGE)"
trap remove_container EXIT

copy_sources_to_workdir
prepare_environment

if [ "$1" == "--shell" ];then
    container_shell
    exit 0
fi

run_integration_tests