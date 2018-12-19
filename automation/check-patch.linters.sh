#!/bin/bash -xe

source automation/common.sh

prepare_env
install_dependencies
build_vdsm

make lint
