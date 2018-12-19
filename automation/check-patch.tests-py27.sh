#!/bin/bash -xe

source automation/common.sh

prepare_env
install_dependencies
build_vdsm
run_tests py27
generate_combined_coverage_report py27
