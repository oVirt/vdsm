#!/bin/bash

source automation/common.sh

prepare_env
install_dependencies
build_vdsm

function collect_logs {
    res=$?
    [ "$res" -ne 0 ] && echo "*** err: $res"
    cd /var/log
    tar --exclude "journal/*" -czf "$EXPORT_DIR/mock_varlogs.tar.gz" *
    cd /var/host_log
    tar --exclude "journal/*" -czf "$EXPORT_DIR/host_varlogs.tar.gz" *
}

trap collect_logs EXIT

debuginfo-install -y python

# Make sure we have enough loop device nodes.
create_loop_devices 8

TIMEOUT=600 make check NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib"

# Generate coverage report in HTML format
pushd tests
pwd
ls .cov*
coverage combine .coverage-nose-py2 .coverage-storage-py27 .coverage-network-py27 .coverage-virt-py27 .coverage-lib-py27
coverage html -d "$EXPORT_DIR/htmlcov"

if grep -q 'Fedora' /etc/redhat-release; then
    rm .coverage
    coverage combine .coverage-nose-py3 .coverage-storage-py36 .coverage-network-py36 .coverage-virt-py36 .coverage-lib-py36
    coverage html -d "$EXPORT_DIR/htmlcov-py36"
fi
popd

# Export subsystem coverage reports for viewing in jenkins.
mv tests/htmlcov-* "$EXPORT_DIR"
