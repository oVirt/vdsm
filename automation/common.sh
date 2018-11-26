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
    pip install -U tox==2.9.1
}

build_vdsm() {
    if [ ! -f Makefile ]; then
      ./autogen.sh --system --enable-hooks --enable-vhostmd
    fi

    make
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
