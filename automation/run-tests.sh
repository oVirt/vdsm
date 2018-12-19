#!/bin/bash -xe

source automation/common.sh

PYTHON_VERSION="$1"

prepare_env
install_dependencies
build_vdsm
run_tests "$PYTHON_VERSION"
generate_combined_coverage_report "$PYTHON_VERSION"
