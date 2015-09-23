#!/bin/bash

set -xe

# this redefines 'ugly' but looks like NOSE_EXCLUDE works at test method level,
# not at module neither at testcase level, so we have no choice but this.
export NOSE_EXCLUDE="\
.*testGetBondingOptions.*|\
testMirroring.*|\
testToggleIngress|\
testException|\
testQdiscsOfDevice|\
testReplacePrio\
"

./autogen.sh --system --enable-hooks
make check

./automation/build-artifacts.sh

# enable complex globs
shopt -s extglob
# if specfile was changed, try to install all created packages
if git diff-tree --no-commit-id --name-only -r HEAD | grep --quiet 'vdsm.spec.in' ; then
    yum -y install exported-artifacts/!(*.src).rpm
fi
