#!/bin/bash

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

LOGFILE="/var/log/vdsm/spm-lock.log"
KILL="/bin/kill"
sdUUID="$1"

function usage() {
    if [ -n "$1" ]; then
        echo $1
    fi
    echo "usage: $0 { sdUUID }"
    echo "  sdUUID - storage domain uuid"
    exit 1
}

function log() {
    echo "[`date +"%F %T"`] $*" >> $LOGFILE
}

if [ "$#" -lt 1 ]; then
    usage
fi

spmprotect_pgrps=$(
    ps -o pgrp= -o cmd= -C spmprotect.sh | grep renew | grep "$sdUUID" | \
	awk '{ print -$1 }' | sort -n | uniq
)
spmprotect_pgrps_len=$(echo $spmprotect_pgrps | wc -w)

if [[ -z "$spmprotect_pgrps" ]]; then
    log "No process found to kill"
    exit 0
else
    log "Stopping lease for domain: $sdUUID pgrps: $spmprotect_pgrps"
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
