#!/bin/bash -e

# enable complex globs
shopt -s extglob

ARTIFACTS_DIR=$1

export LC_ALL=C.UTF8  # no idea why this is suddenly needed

rpmlint "$ARTIFACTS_DIR/"*.src.rpm

# TODO: fix spec to stop ignoring the few current errors
! rpmlint "$ARTIFACTS_DIR/"!(*.src).rpm | \
    grep ': E: ' | \
    grep -v explicit-lib-dependency | \
    grep -v no-binary | \
    grep -v non-readable | grep -v non-standard-dir-perm
