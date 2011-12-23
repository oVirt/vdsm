#!/bin/bash
#
# Copyright 2006-2010 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

function isOvirt() {
    if [ -f /etc/rhev-hypervisor-release ]; then
        return 0
    elif [ -f /etc/ovirt-node-image-release ]; then
        return 0
    else
        return 1
    fi
}

# execute a function if called as a script, e.g.
# vdsm-bash-functions isOvirt

if [ "$(basename -- "$0")" = "vdsm-bash-functions" ]; then
    "$@"
fi
