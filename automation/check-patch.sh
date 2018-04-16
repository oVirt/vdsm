#!/bin/bash

source automation/ovirt.sh

EXPORT_DIR="$PWD/exported-artifacts"
mkdir -p $EXPORT_DIR

set -xe

# For skipping known failures on jenkins using @broken_on_ci
export OVIRT_CI=1

pip install -U tox==2.9.1

./autogen.sh --system --enable-hooks --enable-vhostmd
make

debuginfo-install -y python

# Make sure we have enough loop device nodes.
create_loop_devices 8

TIMEOUT=600 make --jobs=2 check NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib"

# Generate coverage report in HTML format
pushd tests
pwd
ls .cov*
coverage combine .coverage-nose-py2 .coverage-storage-py27 .coverage-network-py27 .coverage-virt-py27
coverage html -d "$EXPORT_DIR/htmlcov"

if grep -q 'Fedora' /etc/redhat-release; then
    rm .coverage
    coverage combine .coverage-nose-py3 .coverage-storage-py36 .coverage-network-py36 .coverage-virt-py36
    coverage html -d "$EXPORT_DIR/htmlcov-py36"
fi
popd

# Export subsystem coverage reports for viewing in jenkins.
mv tests/htmlcov-* "$EXPORT_DIR"

# In case of vdsm specfile or any Makefile.am file modification in commit,
# try to build and install all new created packages
if git diff-tree --no-commit-id --name-only -r HEAD | egrep --quiet 'vdsm.spec.in|Makefile.am|automation' ; then
    ./automation/build-artifacts.sh

    tests/check_distpkg.sh $(ls $EXPORT_DIR/vdsm*.tar.gz)
    tests/check_rpms.sh $EXPORT_DIR

    create_artifacts_repo $EXPORT_DIR

    vr=$(build-aux/pkg-version --version)-$(build-aux/pkg-version --release)

    yum -y install vdsm-$vr\* vdsm-client-$vr\* vdsm-hook-\*-$vr\* vdsm-tests-$vr\* vdsm-gluster-$vr\*
fi

function collect_logs {
    cd /var/log
    tar -cvzf "$EXPORT_DIR/mock_varlogs.tar.gz" *
    cd /var/host_log
    tar -cvzf "$EXPORT_DIR/host_varlogs.tar.gz" *
}

collect_logs
