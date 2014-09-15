#!/bin/sh
DOMAIN=$1
EVENT=$2
PHASE=$3

# This condition depends on a feature that will be included in
# libvirt once https://bugzilla.redhat.com/show_bug.cgi?id=1142684
# is resolved. It will work only for migrations before that.
if [ "x$EVENT" != "xmigrate" -a "x$EVENT" != "xrestore" ]; then
  # Return 0 and empty output for events that are not handled
  # by this hook.
  #
  # libvirt will use input XML without change and consider
  # it a success run according to the documentation.
  exit 0
fi

# Fix VMs migrating to host with libvirt >= 1.2.8
# See https://bugzilla.redhat.com/show_bug.cgi?id=1138340
exec sed -e 's|<min_guarantee[^>]*>[0-9 ]*</min_guarantee>||g'

