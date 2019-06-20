#!/bin/bash

set -xe

# Common helpers

prepare_env() {
    # For skipping known failures on jenkins using @broken_on_ci
    export OVIRT_CI=1
    export BUILDS=$PWD/rpmbuild
    export EXPORT_DIR="$PWD/exported-artifacts"
    mkdir -p $EXPORT_DIR
}

install_dependencies() {
    tests/profile pip-install pip install -U tox==2.9.1
}

build_vdsm() {
    if [ ! -f Makefile ]; then
      tests/profile autogen ./autogen.sh --system --enable-hooks --enable-vhostmd
    fi

    tests/profile make make
}

# oVirt CI helper functions

create_loop_devices() {
    local last=$(($1-1))
    local min
    for min in `seq 0 $last`; do
        local name=/dev/loop$min
        if [ ! -e "$name" ]; then
            mknod --mode 0666 $name b 7 $min
        fi
    done
}

create_artifacts_repo() {
    local repo="$1"

    createrepo "$repo"

    # Some slaves have /etc/dnf/dnf.conf when running el7 build - patch both
    # yum.conf and dnf.conf to make sure our repo is found.
    local url="file://$repo"
    for conf in /etc/yum.conf /etc/dnf/dnf.conf; do
        if [ -f "$conf" ]; then
            cat automation/artifacts.repo | sed -e "s#@BASEURL@#$url#" >> "$conf"
        fi
    done
}

check_install() {
    if [ -z "$EXPORT_DIR" ]; then
        (>&2 echo "*** EXPORT_DIR must be set to run check_install!")
        exit 1
    fi

    tests/profile build-artifacts $PWD/automation/build-artifacts.sh

    tests/check_distpkg.sh "$(ls "$EXPORT_DIR"/vdsm*.tar.gz)"
    tests/check_rpms.sh "$EXPORT_DIR"

    create_artifacts_repo "$EXPORT_DIR"

    local vr=$(build-aux/pkg-version --version)-$(build-aux/pkg-version --release)

    if grep -q 'Fedora' /etc/redhat-release; then
        DNF=dnf
    else
        DNF=yum
    fi

    tests/profile install "$DNF" -y install vdsm-$vr\* vdsm-client-$vr\* vdsm-hook-\*-$vr\* vdsm-tests-$vr\* vdsm-gluster-$vr\*
}

generate_combined_coverage_report() {
    local python_version="$1"

    pushd tests
    pwd
    ls .cov*
    coverage combine ".coverage-nose-${python_version::-1}" \
                     ".coverage-storage-$python_version" \
                     ".coverage-network-$python_version" \
                     ".coverage-virt-$python_version" \
                     ".coverage-lib-$python_version"

    ./profile "coverage-$python_version" coverage html -d "$EXPORT_DIR/htmlcov"
    popd

    # Export subsystem coverage reports for viewing in jenkins.
    mv tests/htmlcov-* "$EXPORT_DIR"
}

teardown() {
    res=$?
    [ "$res" -ne 0 ] && echo "*** err: $res"

    # NOTE: Tar fails randomly when some log file is modified while tar is
    # reading it, and there is no way to detet and filter this failure.
    # We also do not want to fail the build if log collections failed.

    tar --directory /var/log \
        --exclude "journal/*" \
        -czf "$EXPORT_DIR/mock_varlogs.tar.gz" \
        . || echo "WARNING: Ignoring error collecting logs in /var/log"

    tar --directory /var/host_log \
        --exclude "journal/*" \
        -czf "$EXPORT_DIR/host_varlogs.tar.gz" \
        . || echo "WARNING: Ignoring error collecting logs in /var/host_log"

    # We must teardown loop devices and mounts, otherwise mock fail to remove
    # the mount directories:
    # OSError: [Errno 16] Device or resource busy:
    # '/var/lib/mock/epel-7-x86_64-2ff84fd1f104757319d3f4d8e9603805-15751/root/var/tmp/vdsm-storage/mount.file-512'

    python2 tests/storage/userstorage.py teardown \
        || echo "WARNING: Ingoring error while tearing down user storage"
}

install_lvmlocal_conf() {
    mkdir -p /etc/lvm
    cp docker/lvmlocal.conf /etc/lvm/
}

run_tests() {
    local python_version="$1"

    if [ -z "$EXPORT_DIR" ]; then
        (>&2 echo "*** EXPORT_DIR must be set to run tests!")
        exit 1
    fi

    trap teardown EXIT

    tests/profile debuginfo-install debuginfo-install -y python

    # Make sure we have enough loop device nodes. Using 16 devices since with 8
    # devices we have random mount failures.
    create_loop_devices 16

    install_lvmlocal_conf

    # Set up storage for storage tests (tore down in teardown()).
    python2 tests/storage/userstorage.py setup

    TIMEOUT=600 make "tests-$python_version" NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib"
}
