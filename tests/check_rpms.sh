#!/bin/bash -e

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
    grep -v non-readable | \
    grep -v non-standard-dir-perm | \
    grep -v missing-dependency-to-cron # FIXME: missing-dependency-to-cron check is broken in rpmlint 1.11, valid check is available since ver. 2.1
