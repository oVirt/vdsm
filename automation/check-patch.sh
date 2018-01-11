#!/bin/bash

source automation/ovirt.sh

check-distpkg() {
    DIST=$(ls $EXPORT_DIR/vdsm*.tar.gz)
    if test -z "$DIST" ; then
        echo "ERROR: Distribution package not created by build-artifacts.sh"
        exit 1
    fi

    # Verify that generated files are not included in dist package
    DIST_LIST=$(mktemp)
    DIR=$(basename "$DIST" .tar.gz)
    tar tzf "$DIST" > "$DIST_LIST"
    for i in $(git ls-files \*.in); do
        FILE=$(echo "$i" | sed -e 's/.in$//')

        # There are some files that we want to be included
        KEEP=0
        for f in \
            static/libexec/vdsm/vdsm-gencerts.sh \
            static/usr/share/man/man1/vdsm-tool.1 \
            tests/run_tests.sh \
            tests/run_tests_local.sh \
            vdsm.spec \
        ; do
            if test "$FILE" = "$f" ; then
                KEEP=1
                break
            fi
        done
        test "$KEEP" -eq 1 && continue

        if grep -q -F -x "$DIR/$FILE" "$DIST_LIST"; then
            echo "ERROR: Distribution package contains generated file $FILE"
            exit 1
        fi
    done
    rm -f "$DIST_LIST"
}

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
if git diff-tree --no-commit-id --name-only -r HEAD | egrep --quiet 'vdsm.spec.in|Makefile.am' ; then
    ./automation/build-artifacts.sh

    check-distpkg

    createrepo $EXPORT_DIR

    # Some slaves have /etc/dnf/dnf.conf when running el7 build - patch both
    # yum.conf and dnf.conf to make sure our repo is found.
    for conf in /etc/yum.conf /etc/dnf/dnf.conf; do
        if [ -f "$conf" ]; then
            cat automation/artifacts.repo | sed -e "s#@BASEURL@#file://$EXPORT_DIR#" >> "$conf"
        fi
    done

    vr=$(build-aux/pkg-version --version)-$(build-aux/pkg-version --release)

    yum -y install vdsm-$vr\* vdsm-client-$vr\* vdsm-hook-\*-$vr\* vdsm-tests-$vr\*

    export LC_ALL=C  # no idea why this is suddenly needed
    rpmlint "$EXPORT_DIR/"*.src.rpm

    # TODO: fix spec to stop ignoring the few current errors
    ! rpmlint "$EXPORT_DIR/"!(*.src).rpm | grep ': E: ' | grep -v explicit-lib-dependency | \
        grep -v no-binary | \
        grep -v non-readable | grep -v non-standard-dir-perm
fi
