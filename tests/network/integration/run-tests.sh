#!/bin/bash -xe

source tests/network/common.sh

CONTAINER_IMAGE="${CONTAINER_IMAGE:=ovirt/$PROJECT-test-integration-network-centos-8}"

load_kernel_modules

CONTAINER_ID="$($CONTAINER_CMD run --privileged -d -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z --env PYTHONPATH=lib $CONTAINER_IMAGE)"
trap remove_container EXIT

setup_vdsm_sources_for_testing

if [ "$1" == "--shell" ];then
    container_shell
    exit 0
fi

run_tests integration
