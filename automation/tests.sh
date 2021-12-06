#!/bin/bash -xe

# Enable IPv6
echo 0 > /proc/sys/net/ipv6/conf/all/disable_ipv6

./autogen.sh --system
make
make tests
