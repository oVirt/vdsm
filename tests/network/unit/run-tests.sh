#!/bin/bash -xe

source tests/network/common.sh

IMAGE_TAG="${IMAGE_TAG:=centos-8}"
CONTAINER_IMAGE="${CONTAINER_IMAGE:=$IMAGE_PREFIX-unit}"

function patch_dist_commons {
    container_exec "
        cp $VDSM_WORKDIR/vdsm/tests/network/static/constants.py $VDSM_WORKDIR/vdsm/lib/vdsm/common \
        && \
        cp $VDSM_WORKDIR/vdsm/tests/network/static/config.py $VDSM_WORKDIR/vdsm/lib/vdsm/common \
        && \
        cp $VDSM_WORKDIR/vdsm/tests/network/static/dsaversion.py $VDSM_WORKDIR/vdsm/lib/vdsm/common
    "
}

CONTAINER_ID="$($CONTAINER_CMD run -d -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z --env PYTHONPATH=lib $CONTAINER_IMAGE:$IMAGE_TAG)"
trap remove_container EXIT

copy_sources_to_workdir
patch_dist_commons

if [ "$1" == "--shell" ];then
    container_shell
    exit 0
fi

run_tests unit
