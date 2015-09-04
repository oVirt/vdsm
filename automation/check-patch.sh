#!/bin/bash -e
#
# Run on each patch to gerrit, should be faster than check-meged and require
# less resources but thorough enough to provide relevant feedback

# Nose 1.3.0 and later segatult with this flag
#export NOSE_WITH_XUNIT=1

export NOSE_SKIP_STRESS_TESTS=1
# this redefines 'ugly' but looks like NOSE_EXCLUDE works at test method level,
# not at module neither at testcase level, so we have no choice but this.
export NOSE_EXCLUDE=".*testGetBondingOptions.*|testMirroring.*|testToggleIngress|testException|testQdiscsOfDevice|testReplacePrio"

sh -x autogen.sh --system
make all
make check
