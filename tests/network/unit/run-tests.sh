#!/bin/bash -xe

PROJECT=${PROJECT:-${PWD##*/}}
PROJECT_PATH="$PWD"
CONTAINER_WORKSPACE="/workspace/$PROJECT"
CONTAINER_IMAGE="${CONTAINER_IMAGE:=ovirt/$PROJECT-test-unit-network-centos-8}"
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
        mkdir $VDSM_WORKDIR && \
        cp -rf $CONTAINER_WORKSPACE $VDSM_WORKDIR/
    "
}

function patch_dist_commons {
    container_exec "
        cp $VDSM_WORKDIR/vdsm/tests/network/static/constants.py $VDSM_WORKDIR/vdsm/lib/vdsm/common \
        && \
        cp $VDSM_WORKDIR/vdsm/tests/network/static/config.py $VDSM_WORKDIR/vdsm/lib/vdsm/common \
        && \
        cp $VDSM_WORKDIR/vdsm/tests/network/static/dsaversion.py $VDSM_WORKDIR/vdsm/lib/vdsm/common
    "
}

function run_unit_tests {
    container_exec "
        cd $VDSM_WORKDIR/$PROJECT \
        && \
        pytest -vv tests/network/unit
    "
}

CONTAINER_ID="$($CONTAINER_CMD run -d -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z --env PYTHONPATH=lib $CONTAINER_IMAGE)"
trap remove_container EXIT

copy_sources_to_workdir
patch_dist_commons

if [ "$1" == "--shell" ];then
    container_shell
    exit 0
fi

run_unit_tests
