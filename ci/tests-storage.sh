#!/bin/sh -e

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

print_help() {
    echo "Usage: $0 USER"
    echo ""
    echo "  Helper script to setup and run storage tests as USER."
    echo ""
    echo "Examples:"
    echo "  Run tests as root user"
    echo "    $ $0 root"
    echo "  Run tests as vdsm user"
    echo "    $ $0 vdsm"
}

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
    # Configure lvm to ignore udev events, otherwise some lvm tests hang.
    mkdir -p /etc/lvm
    cp docker/lvmlocal.conf /etc/lvm/

    # Make sure we have enough loop device nodes. Using 16 devices since with 8
    # devices we have random mount failures.
    create_loop_devices 16

    # Build vdsm.
    ./autogen.sh --system
    make

    # Setup user storage during the tests.
    make storage
}

teardown_storage() {
    make clean-storage \
        || echo "WARNING: Ingoring error while tearing down user storage"
}

# Process user argument
user=$1
if [ -z "$user" ]; then
    echo "ERROR: user required"
    print_help
    exit 1
fi

# Only when running in a container
[ -d /venv ] && {
    # Workaround to avoid this warning:
    #   fatal: detected dubious ownership in repository at '/dir'
    git config --global --add safe.directory "$(pwd)"

    # Activate the tests venv (for containers only)
    source /venv/bin/activate
}

# Force colored output for storage tests
export FORCE_COLOR=1
export PY_COLORS=1

echo "Running tests as user $user"

setup_storage
if [ "$user" != "root" ]; then
    # Change ownership of current and storage folders
    # to allow non-privileged access.
    chown -R $user ./ /var/tmp/vdsm*
fi
# Teardown storage before exit.
trap teardown_storage EXIT

if [ "$user" = "root" ]; then
    # Run only tests marked as root
    make tests-storage-root
else
    # Run tests not marked as root as $user
    # Use 'su' instead of 'sudo' in order to preserve the environment.
    su $user -c "make tests-storage-user"
fi
