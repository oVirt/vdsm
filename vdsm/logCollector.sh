#!/bin/bash
#
# Copyright 2009-2011 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
# Description:	  Logs collector for RHEV
# Input:
#       uuid: added to output file for uniquness.
#
BASEDIR=/var/log
TMPDIR=$BASEDIR/qlogs
ERR=$TMPDIR/collectErrors.log
DESTINATION=$BASEDIR/qlogs-${1}.tar.xz

if [ -x $TMPDIR ]; then
    rm -rf $TMPDIR
fi

rm -f $BASEDIR/qlogs-*.tar.xz

touch $DESTINATION
mkdir -p $TMPDIR
echo `date` >> $ERR

/usr/sbin/sosreport --batch --tmp-dir="$TMPDIR" \
	-o libvirt,vdsm,general,networking,hardware,process,yum,filesys,devicemapper,selinux,kernel $@\
	>> $ERR 2>&1
RETVAL=$?

if [ "$RETVAL" -eq 0 ]; then
    mv $TMPDIR/*.tar.xz $DESTINATION
    rm -rf $TMPDIR
    exit 0
else
    echo "Could not archive logs" | /usr/bin/tee -a >> "$ERR"
    exit 1
fi

