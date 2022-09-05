#!/bin/bash -xe

# Enable IPv6
echo 0 > /proc/sys/net/ipv6/conf/all/disable_ipv6
# Load bonding module
SUFFIX=$RANDOM
ip link add mod_bond$SUFFIX type bond && ip link del mod_bond$SUFFIX

./autogen.sh --system
make
make tests
