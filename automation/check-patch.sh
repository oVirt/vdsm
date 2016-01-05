#!/bin/bash

set -xe

# Nose 1.3.0 and later segatult with this flag
#export NOSE_WITH_XUNIT=1

export NOSE_SKIP_STRESS_TESTS=1
export NOSE_EXCLUDE="\
test_discarded_workers|\
testBridgeEthtoolDrvinfo|\
testGetLink|\
testMethodMissingMethod|\
testMirroring|\
testMonitorIteration|\
testTcException|\
testToggleIngress|\
testTogglePromisc|\
testQdiscsOfDevice|\
testReplacePrio\
"

# really ugly and hopefully temporary fix
# https://bugzilla.redhat.com/show_bug.cgi?id=1255142
[[ -e /dev/net/tun ]] \
|| {
    [[ -e /dev/net ]] || mkdir /dev/net
    mknod /dev/net/tun c 10 200
}

./autogen.sh --system --enable-hooks
make check

./automation/build-artifacts.sh

# if specfile was changed, try to install all created packages
if git diff-tree --no-commit-id --name-only -r HEAD | grep --quiet 'vdsm.spec.in' ; then
  yum -y install exported-artifacts/*.rpm
fi
