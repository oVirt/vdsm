#!/bin/bash -xe

source automation/common_network.sh

init
trap collect_and_clean EXIT
setup_env
fake_ksm_in_vm
run_test run_functional_network_test_ovs_switch
