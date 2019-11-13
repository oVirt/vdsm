#!/bin/bash

set -xe

# Common helpers

prepare_env() {
    # For skipping known failures on jenkins using @broken_on_ci
    export OVIRT_CI=1
    export BUILDS=$PWD/rpmbuild
    export EXPORT_DIR="$PWD/exported-artifacts"
    export PATH="/usr/local/bin:$PATH"
    mkdir -p $EXPORT_DIR
}

install_dependencies() {
    ${CI_PYTHON} tests/profile pip-upgrade ${CI_PYTHON} -m pip \
        install --upgrade pip

    ${CI_PYTHON} tests/profile pip-install ${CI_PYTHON} -m pip \
        install --upgrade "tox==3.14"
}

build_vdsm() {
    if [ ! -f Makefile ]; then
        ${CI_PYTHON} tests/profile autogen ./autogen.sh \
            --system \
            --enable-hooks \
            --enable-vhostmd
    fi

    ${CI_PYTHON} tests/profile make make
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

    ${CI_PYTHON} tests/profile build-artifacts $PWD/automation/build.sh

    tests/check_distpkg.sh "$(ls "$EXPORT_DIR"/vdsm*.tar.gz)"
    tests/check_rpms.sh "$EXPORT_DIR"

    create_artifacts_repo "$EXPORT_DIR"

    local vr=$(build-aux/pkg-version --version)-$(build-aux/pkg-version --release)

    if [ -x /usr/bin/dnf ]; then
        DNF=dnf
    else
        DNF=yum
    fi

    ${CI_PYTHON} tests/profile install "$DNF" -y install \
        vdsm-$vr\* \
        vdsm-client-$vr\* \
        vdsm-hook-\*-$vr\* \
        vdsm-gluster-$vr\*
}

generate_combined_coverage_report() {
    pushd tests
    pwd
    ls .cov*

    ${CI_PYTHON} -m coverage combine .coverage-*
    ${CI_PYTHON} ./profile coverage ${CI_PYTHON} -m coverage html -d "$EXPORT_DIR/htmlcov"
    popd

    # Export subsystem coverage reports for viewing in jenkins.
    mv tests/htmlcov-* "$EXPORT_DIR"
}

teardown() {
    res=$?
    [ "$res" -ne 0 ] && echo "*** err: $res"

    collect_logs
    teardown_storage
}

install_lvmlocal_conf() {
    mkdir -p /etc/lvm
    cp docker/lvmlocal.conf /etc/lvm/
}

# Query package info and return "version-release" string.
package_version() {
    # Using slow "rpm -qa" since on CentOS 7 "rpm -q" always succeeds and
    # writes "package python2 is not installed" to stdout, while "rpm -qa"
    # returns empty string if the package is not installed.
    rpm -qa --queryformat "%{VERSION}-%{RELEASE}" $1
}

install_python_debuginfo() {
    local pkg_name=${CI_PYTHON}
    local pkg_ver="$(package_version ${pkg_name})"

    # 'CI_PYTHON' is either "python2" or "python3", but on CentOS we don't have
    # "python2" package and we must query and install "python" and
    # "python-debuginfo" packages.
    if [ "${pkg_name}" = "python2" -a -z "$pkg_ver" ]; then
        pkg_name="python"
        pkg_ver="$(package_version ${pkg_name})"
    fi

    ${CI_PYTHON} tests/profile debuginfo-install debuginfo-install -y ${pkg_name}

    local pkg_dbg_ver="$(package_version ${pkg_name}-debuginfo)"

    if [ -z "${pkg_dbg_ver}" -o "${pkg_ver}" != "${pkg_dbg_ver}" ]; then
        echo "WARNING: ${pkg_name}-debuginfo-${pkg_dbg_ver} doesn't match ${pkg_name}-${pkg_ver}!" \
            "Backtraces for timed-out tests won't be available!"
    fi
}

run_tests() {
    if [ -z "$EXPORT_DIR" ]; then
        (>&2 echo "*** EXPORT_DIR must be set to run tests!")
        exit 1
    fi

    trap teardown EXIT

    install_python_debuginfo

    # Make sure we have enough loop device nodes. Using 16 devices since with 8
    # devices we have random mount failures.
    create_loop_devices 16

    install_lvmlocal_conf

    setup_storage

    # Jenkins slaves randomly time out storage tests with 600 seconds timeout.
    # Use larger timeout to avoid failures when the slaves are too slow.
    make tests NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib" TIMEOUT=600 STORAGE_TIMEOUT=1200
}

# Set up storage for storage tests. The storage is tore down in teardown().
setup_storage() {
    ${CI_PYTHON} tests/storage/userstorage.py setup
}

# Teardown storage set up in setup_storage.
# We must teardown loop devices and mounts, otherwise mock fail to remove the
# mount directories:
# OSError: [Errno 16] Device or resource busy:
# '/var/lib/mock/epel-7-x86_64-2ff84fd1f104757319d3f4d8e9603805-15751/root/var/tmp/vdsm-storage/mount.file-512'
# NOTE: should be called only in teardown context. Always succeeds, even if
# tearing down storage failed.
teardown_storage() {
    ${CI_PYTHON} tests/storage/userstorage.py teardown \
        || echo "WARNING: Ingoring error while tearing down user storage"
}

# Collect tests logs.
# NOTE: should be called only in teardown context. Always succeeds, even if
# collecting logs failed.
collect_logs() {
    # NOTE: Tar fails randomly when some log file is modified while tar is
    # reading it, and there is no way to detect and filter this failure.
    # We also do not want to fail the build if log collections failed.

    tar --directory /var/log \
        --exclude "journal/*" \
        -czf "$EXPORT_DIR/mock_varlogs.tar.gz" \
        . || echo "WARNING: Ignoring error collecting logs in /var/log"

    tar --directory /var/host_log \
        --exclude "journal/*" \
        -czf "$EXPORT_DIR/host_varlogs.tar.gz" \
        . || echo "WARNING: Ignoring error collecting logs in /var/host_log"
}
