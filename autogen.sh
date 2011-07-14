#!/bin/sh

autoreconf -if

if test "x$1" = "x--system"; then
    ./configure --prefix=/usr --sysconfdir=/etc \
                --localstatedir=/var --libdir=/usr/lib
fi
