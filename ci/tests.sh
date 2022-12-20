#!/bin/bash -xe

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

# Enable IPv6
echo 0 > /proc/sys/net/ipv6/conf/all/disable_ipv6
# Load bonding module
SUFFIX=$RANDOM
ip link add mod_bond$SUFFIX type bond && ip link del mod_bond$SUFFIX

# Activate the tests venv (for containers only)
[ -d /venv ] && source /venv/bin/activate

./autogen.sh --system
make
make tests
