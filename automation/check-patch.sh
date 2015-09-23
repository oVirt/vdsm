#!/bin/bash

set -xe

# Nose 1.3.0 and later segatult with this flag
#export NOSE_WITH_XUNIT=1

export NOSE_SKIP_STRESS_TESTS=1


./autogen.sh --system --enable-hooks
make check

./automation/build-artifacts.sh

# enable complex globs
shopt -s extglob
# if specfile was changed, try to install all created packages
if git diff-tree --no-commit-id --name-only -r HEAD | grep --quiet 'vdsm.spec.in' ; then
    yum -y install exported-artifacts/!(*.src).rpm
fi
