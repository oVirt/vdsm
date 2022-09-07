#!/bin/sh -e

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

autoreconf -if

if test "x$1" = "x--system"; then
    shift
    prefix=/usr
    libdir=$prefix/lib
    sysconfdir=/etc
    localstatedir=/var
    if [ -d /usr/lib64 ]; then
      libdir=$prefix/lib64
    fi
    EXTRA_ARGS="--prefix=$prefix --sysconfdir=$sysconfdir --localstatedir=$localstatedir --libdir=$libdir"
    echo "Running ./configure with $EXTRA_ARGS $@"
else
    if test -z "$*" && test ! -f "$THEDIR/config.status"; then
        echo "I am going to run ./configure with no arguments - if you wish "
        echo "to pass any to it, please specify them on the $0 command line."
    fi
fi

if test -z "$*" && test -f config.status; then
    ./config.status --recheck
else
    ./configure $EXTRA_ARGS "$@"
fi && {
    echo
    echo "Now type 'make' to compile vdsm."
}
