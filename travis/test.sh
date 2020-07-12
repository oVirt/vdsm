#!/bin/bash -e
# Usage:
#   test.sh target1 target2 ...

source automation/common.sh

create_loop_devices 16
./autogen.sh --system
make all $@
