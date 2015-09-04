#!/bin/bash -e
#
# Run on each patch to gerrit, should be faster than check-meged and require
# less resources but thorough enough to provide relevant feedback

# Nose 1.3.0 and later segatult with this flag
#export NOSE_WITH_XUNIT=1

export NOSE_SKIP_STRESS_TESTS=1
export NOSE_EXCLUDE=

# really ugly and hopefully temporary fix
# https://bugzilla.redhat.com/show_bug.cgi?id=1255142
[[ -e /dev/net/tun ]] \
|| {
    [[ -e /dev/net ]] || mkdir /dev/net
    mknod /dev/net/tun c 10 200
}

sh -x autogen.sh --system
make all
make check
