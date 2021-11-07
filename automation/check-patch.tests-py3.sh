#!/bin/bash -xe

source automation/common.sh

prepare_env
install_dependencies
report_packages_versions
build_vdsm
run_tests
generate_combined_coverage_report
export_artifacts
