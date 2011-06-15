#!/bin/bash

set +o pipefail

SETSID="/usr/bin/setsid"
LOGFILE="/var/log/vdsm/spm-lock.log"
VDS_CLIENT="/usr/bin/vdsClient"
LEASE_UTIL="./safelease"
KILL="/bin/kill"
PKILL="/usr/bin/pkill"
spUUID=$2
RESTARTVDSCMD=${RESTARTVDSCMD:-"sudo /sbin/service vdsmd restart"}
STARTVDSCMD=${STARTVDSCMD:-"sudo /sbin/service vdsmd start"}
CHECKVDSM=${CHECKVDSM:-"/usr/bin/pgrep vdsm"}
REBOOTCMD=${REBOOTCMD:-"sudo /sbin/reboot -f"}
RENEWDIR="/var/run/vdsm/spmprotect/$$"
VDSM_PIDFILE="/var/run/vdsm/vdsmd.pid"
VDSM_PID=`/bin/cat "$VDSM_PIDFILE"`

function usage() {
    if [ -n "$1" ]; then
        echo $1
    fi
    trap EXIT
    echo "usage: $0 COMMAND PARAMETERS"
    echo "Commands:"
    echo "  start { spUUID hostId renewal_interval_sec lease_path[:offset] lease_time_ms io_op_timeout_ms fail_retries }"
    echo "Parameters:"
    echo "  spUUID -                pool uuid"
    echo "  hostId -                host id in pool"
    echo "  renewal_interval_sec -  intervals for lease renewals attempts"
    echo "  lease_path -            path to lease file/volume"
    echo "  offset -                offset of lease within file"
    echo "  lease_time_ms -         time limit within which lease must be renewed (at least 2*renewal_interval_sec)"
    echo "  io_op_timeout_ms -      I/O operation timeout"
    echo "  fail_retries -          Maximal number of attempts to retry to renew the lease before fencing (<= lease_time_ms/renewal_interval_sec)"
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
}

function fence() {
    trap "" EXIT
    trap "" INT

    log "Fencing spUUID=$spUUID id=$ID lease_path=$LEASE_FILE"
    (sleep 20 && echodo $REBOOTCMD) &
    disown
    (sleep 7
        log "Trying to stop vdsm for spUUID=$spUUID id=$ID lease_path=$LEASE_FILE"
        echodo $RESTARTVDSCMD
    )&
    disown

    echodo $KILL -USR1 "$VDSM_PID"

    rm -fr $RENEWDIR
    trap EXIT
    exit 3
}

function echodo() {
	echo $*
	eval $*
}

function release() {
    trap "" EXIT
    trap "" INT
    trap "" USR1
    log "releasing lease spUUID=$spUUID id=$ID lease_path=$LEASE_FILE"
    $KILL -USR1 0
    $LEASE_UTIL release $LEASE_FILE $ID
    rm -fr $RENEWDIR
    exit 0
}

function fail() {
    trap EXIT
    exit 1
}

function validate_args() {
    if [ ! -w "$3" ]; then
        usage "error - lease file does not exist or is not writeable"
    fi
    ID="$1"
    RENEWAL_INTERVAL="$2"
    LEASE_FILE="$3"
    LEASE_TIME_MS="$4"
    IO_OP_TIMEOUT_MS="$5"
    LAST_RENEWAL="$6"

    # Make sure params are integers
    [ "$RENEWAL_INTERVAL" -eq "$RENEWAL_INTERVAL" 2>/dev/null ] || usage "error - Renewal interval not an integer"
    [ "$LEASE_TIME_MS" -eq "$LEASE_TIME_MS" 2>/dev/null ] || usage "error - Lease time not an integer"
    [ "$LEASE_TIME_MS" -ge $((RENEWAL_INTERVAL*2)) ] || usage "error - Lease time too small"
    [ "$IO_OP_TIMEOUT_MS" -eq "$IO_OP_TIMEOUT_MS" 2>/dev/null ] || usage "error - IO op timeout not an integer"
}

function renew() {
    trap EXIT
    trap INT
    trap USR1

    local renew_ts
    debug "in renew, mpid=$MPID"
    if ! renew_ts=`$LEASE_UTIL $dbg renew $LEASE_FILE $ID $LEASE_TIME_MS $IO_OP_TIMEOUT_MS` ; then
        log "failed renewing lease"
    else
        touch "$RENEWDIR/$renew_ts"
        debug "Lease renewed, TS=$renew_ts"
    fi
}

function check_renew() {
    local latest list
    local res=1
    if ! list=`ls "$RENEWDIR" | sort -n` ; then
        return "$res"
    fi
    if latest=`echo $list | awk '{ print $NF }'` ; then
        if [[ -n "$latest" ]] && [[ "$latest" -gt "$LAST_RENEWAL" ]] ; then
            LAST_RENEWAL="$latest"
            res=0
        fi
    fi
    if pushd "$RENEWDIR" > /dev/null; then
        rm -f $list
        popd > /dev/null
    fi
    return "$res"
}

function start_renewal_loop() {
    local renewed curr i tl TPID
    while true ; do
        curr=`date +%s`
        debug "last renewal = $LAST_RENEWAL, curr = $curr"
        tl=$((LEASE_TIME_MS/1000-(curr*1000000-LAST_RENEWAL)/1000000))
        if [ "$tl" -gt "0" ] ; then
            (sleep $tl && fence) 2>/dev/null &
            disown
            TPID=$!
        else
            fence
        fi

        renewed="no"
        while [ "$renewed" == "no" ] ; do
            renew &
            i=0
            while [ "$i" -lt "10" -a "$renewed" == "no" ] ; do
                i=$((i+1))
                sleep 1
                if check_renew ; then
                    renewed="yes"
                fi
            done
        done
        # kill timer's sleeping child process
        $PKILL -TERM -P $TPID
    done
}


####################################################### Main ###################################################

if [ "$#" -lt 8 ]; then
    usage "error - wrong number of arguments"
fi

validate_args $3 $4 $5 $6 $7 $8
DEBUG="$9"
dbg=""
if [ "$DEBUG" -eq "$DEBUG" 2>/dev/null ]; then
    dbg="-d"
fi

log "Protecting spm lock for vdsm pid $VDSM_PID"

case $1 in
start)
    log "Trying to acquire lease - spUUID=$spUUID lease_file=$LEASE_FILE id=$ID lease_time_ms=$LEASE_TIME_MS io_op_to_ms=$IO_OP_TIMEOUT_MS"
    if ! LAST_RENEWAL=`$LEASE_UTIL $dbg acquire $LEASE_FILE $ID $LEASE_TIME_MS $IO_OP_TIMEOUT_MS`; then
        log "Acquire failed for spUUID=$spUUID id=$ID lease_path=$LEASE_FILE"
        fail
    fi
    log "Lease acquired spUUID=$spUUID id=$ID lease_path=$LEASE_FILE, TS=$LAST_RENEWAL"
    trap fence EXIT
    trap release INT
    trap release USR1

    exec 0>&- && exec 1>&- && exec 2>&- # Close stdin, stdout and stderr
    $SETSID $0 renew $spUUID $ID $RENEWAL_INTERVAL $LEASE_FILE $LEASE_TIME_MS $IO_OP_TIMEOUT_MS $LAST_RENEWAL $DEBUG >> $LOGFILE 2>&1 &
    trap EXIT
    exit 0
    ;;
renew)
    trap fence EXIT
    trap release INT
    trap release USR1

    mkdir -p $RENEWDIR
    log "Started renewal process (pid=$$) for spUUID=$spUUID id=$ID lease_path=$LEASE_FILE"
    start_renewal_loop
    ;;
*)
    usage
    ;;
esac
