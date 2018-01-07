#!/bin/bash

source automation/ovirt.sh

EXPORT_DIR="$PWD/exported-artifacts"
mkdir -p $EXPORT_DIR

collect-logs() {
    cp /var/log/vdsm_tests.log "$EXPORT_DIR"/
}

set -xe

# For skipping known failures on jenkins using @broken_on_ci
export OVIRT_CI=1

pip install -U tox==2.5.0 'pluggy<0.5.0'

./autogen.sh --system --enable-hooks --enable-vhostmd
make

debuginfo-install -y python

# Make sure we have enough loop device nodes.
create_loop_devices 8

trap collect-logs EXIT
TIMEOUT=600 make --jobs=2 check NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib"

# Generate coverage report in HTML format
pushd tests
coverage html -d "$EXPORT_DIR/htmlcov"
popd

# enable complex globs
shopt -s extglob

# In case of vdsm specfile or any Makefile.am file modification in commit,
# try to build and install all new created packages
if git diff-tree --no-commit-id --name-only -r HEAD | egrep --quiet 'vdsm.spec.in|Makefile.am|automation' ; then
    ./automation/build-artifacts.sh

    tests/check_distpkg.sh $(ls $EXPORT_DIR/vdsm*.tar.gz)

    create_artifacts_repo $EXPORT_DIR

    vr=$(build-aux/pkg-version --version)-$(build-aux/pkg-version --release)

    yum -y install vdsm-$vr\* vdsm-client-$vr\* vdsm-hook-\*-$vr\* vdsm-tests-$vr\*

    export LC_ALL=C  # no idea why this is suddenly needed
    rpmlint "$EXPORT_DIR/"*.src.rpm

    # TODO: fix spec to stop ignoring the few current errors
    ! rpmlint "$EXPORT_DIR/"!(*.src).rpm | grep ': E: ' | grep -v explicit-lib-dependency | \
        grep -v no-binary | \
        grep -v non-readable | grep -v non-standard-dir-perm
fi
