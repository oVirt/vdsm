#!/bin/bash -xe

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

# Enable IPv6
echo 0 > /proc/sys/net/ipv6/conf/all/disable_ipv6
# Load bonding module
SUFFIX=$RANDOM
ip link add mod_bond$SUFFIX type bond && ip link del mod_bond$SUFFIX

# Only when running in a container
[ -d /venv ] && {
    # Workaround to avoid this warning:
    #   fatal: detected dubious ownership in repository at '/dir'
    git config --global --add safe.directory "$(pwd)"

    # Activate the tests venv (for containers only)
    source /venv/bin/activate
}

./autogen.sh --system
make
make tests
