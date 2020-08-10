#!/bin/bash

ACTION_KEY="action"
IPADDR_KEY="ip"
IPMASK_KEY="mask"
IPROUTE_KEY="route"
IFACE_KEY="iface"

timeStamp=`date +%s.%N`
DATA_PATH="/run/vdsm/dhclientmon"
DATA_PFILE="$DATA_PATH/$timeStamp"

dhclientmon_config() {
    local cont

    cont="$ACTION_KEY=configure"$'\n'
    if [ -n "$new_ip_address" ]; then
      cont="$cont$IPADDR_KEY=$new_ip_address"$'\n'
    fi
    if [ -n "$new_subnet_mask" ]; then
      cont="$cont$IPMASK_KEY=$new_subnet_mask"$'\n'
    fi
    if [ -n "$interface" ]; then
      cont="$cont$IFACE_KEY=$interface"$'\n'
    fi
    if [ -n "$new_routers" ]; then
      cont="$cont$IPROUTE_KEY=$new_routers"$'\n'
    fi
    echo "$cont" > "$DATA_PFILE"
}

dhclientmon_restore() {
    local cont

    if [ -n "$interface" ] && [ "$reason" == "STOP" ]; then
      cont="$ACTION_KEY=remove"$'\n'
      cont="$cont$IFACE_KEY=$interface"$'\n'
      echo "$cont" > "$DATA_PFILE"
    fi
}
