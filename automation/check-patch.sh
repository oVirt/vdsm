#!/bin/bash

EXPORT_DIR="$PWD/exported-artifacts"

set -xe

# For skipping known failures on jenkins using @broken_on_ci
export OVIRT_CI=1

easy_install pip
pip install -U tox==2.5.0

./autogen.sh --system --enable-hooks --enable-vhostmd
make

# Lint python files changed in this patch. We run this only to show the errors
# as the code is not clean enugh to pass this check.
make pylint-diff || true

debuginfo-install -y python

TIMEOUT=600 make --jobs=2 check NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib"

# Generate coverage report in HTML format
pushd tests
coverage html -d "$EXPORT_DIR/htmlcov"
popd

# enable complex globs
shopt -s extglob
# In case of vdsm specfile or any Makefile.am file modification in commit,
# try to build and install all new created packages
if git diff-tree --no-commit-id --name-only -r HEAD | egrep --quiet 'vdsm.spec.in|Makefile.am' ; then
    ./automation/build-artifacts.sh
    yum -y install "$EXPORT_DIR/"!(*.src).rpm
    export LC_ALL=C  # no idea why this is suddenly needed
    rpmlint "$EXPORT_DIR/"*.src.rpm

    # TODO: fix spec to stop ignoring the few current errors
    ! rpmlint "$EXPORT_DIR/"!(*.src).rpm | grep ': E: ' | grep -v explicit-lib-dependency | \
        grep -v no-binary | \
        grep -v non-readable | grep -v non-standard-dir-perm
fi
