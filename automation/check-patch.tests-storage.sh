#!/bin/bash -xe

source automation/common.sh

prepare_env
install_dependencies
report_packages_versions
build_vdsm
run_tests_storage
export_artifacts
