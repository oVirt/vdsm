#!/bin/sh -e

create_loop_devices() {
    local last=$(($1-1))
    local min
    for min in `seq 0 $last`; do
        local name=/dev/loop$min
        if [ ! -e "$name" ]; then
            mknod --mode 0666 $name b 7 $min
        fi
    done
}

setup_storage() {
    python3 tests/storage/userstorage.py setup
}

teardown_storage() {
    python3 tests/storage/userstorage.py teardown \
        || echo "WARNING: Ingoring error while tearing down user storage"
}

# Configure lvm to ignore udev events, otherwise some lvm tests hang.
mkdir -p /etc/lvm
cp docker/lvmlocal.conf /etc/lvm/

# Make sure we have enough loop device nodes. Using 16 devices since with 8
# devices we have random mount failures.
create_loop_devices 16

# Build vdsm.
./autogen.sh --system
make

# Setup user stoage during the tests.
trap teardown_storage EXIT
setup_storage

make tests-storage
