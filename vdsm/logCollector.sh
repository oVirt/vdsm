#!/bin/bash
#
# Copyright 2009-2010 Red Hat, Inc. All rights reserved.
# Use is subject to license terms.
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

