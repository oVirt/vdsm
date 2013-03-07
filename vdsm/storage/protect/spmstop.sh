#!/bin/bash
#
# Copyright 2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

LOGFILE="/var/log/vdsm/spm-lock.log"
KILL="/bin/kill"
spUUID="$1"
DEBUG="$2"

function usage() {
    if [ -n "$1" ]; then
        echo $1
    fi
    echo "usage: $0 { spUUID }"
    echo "  spUUID -                pool uuid"
    exit 1
}

function debug() {
    if [ -z "$DEBUG" ]; then
            return
    fi
    echo "$*"
}

function log() {
    #logger $*
    echo "[`date +"%F %T"`] $*" >> $LOGFILE
    debug "$*"
}

if [ "$#" -lt 1 ]; then
    usage
fi

spmprotect_pgrps=$(
    ps -o pgrp= -o cmd= -C spmprotect.sh | grep renew | grep "$spUUID" | \
	awk '{ print -$1 }' | sort -n | uniq
)
spmprotect_pgrps_len=$(echo $spmprotect_pgrps | wc -w)

if [[ -z "$spmprotect_pgrps" ]]; then
    debug "No process found to kill"
    exit 0
else
    log "Stopping lease for pool: $spUUID pgrps: $spmprotect_pgrps"
    $KILL -USR1 -- $spmprotect_pgrps >/dev/null 2>&1
fi

for ((i=0; i<10; i+=1)); do
    sleep 1
    killed_len=$($KILL -0 -- $spmprotect_pgrps 2>&1 | wc -l)
    [[ "$killed_len" == "$spmprotect_pgrps_len" ]] && break
done

if [[ "$killed_len" != "$spmprotect_pgrps_len" ]]; then
    $KILL -9 -- $spmprotect_pgrps
fi

exit 0
