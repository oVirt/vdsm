#!/bin/bash -xe

./tests/network/functional/run-tests.sh --switch-type=ovs \
--pytest-args="--skip-stable-link-monitor"
