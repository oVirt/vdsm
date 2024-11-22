#!/bin/bash -xe

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

source tests/network/common.sh

IMAGE_TAG="${IMAGE_TAG:=centos-9}"
CONTAINER_IMAGE="${CONTAINER_IMAGE:=$IMAGE_PREFIX-integration}"

load_kernel_modules

CONTAINER_ID="$($CONTAINER_CMD run --privileged -d -v $PROJECT_PATH:$CONTAINER_WORKSPACE:Z --env PYTHONPATH=lib $CONTAINER_IMAGE:$IMAGE_TAG)"
trap remove_container EXIT

setup_vdsm_sources_for_testing

if [ "$1" == "--shell" ];then
    container_shell
    exit 0
fi

run_tests integration
