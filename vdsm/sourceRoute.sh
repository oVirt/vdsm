#!/bin/bash

timeStamp=`date +%s`

sourceRoute_config() {
    if [ -n "$new_ip_address" ] && [ -n "$new_subnet_mask" ] && \
       [ -n "$new_routers" ] && [ -n  "$interface" ]; then
      echo "configure" "$new_ip_address" "$new_subnet_mask" "$new_routers" \
          "$interface" > /var/run/vdsm/sourceRoutes/$timeStamp
    fi
}

sourceRoute_restore() {
    if [ -n "$interface" ] && [ "$reason" == "STOP" ]; then
      echo "remove" "$interface" > \
          /var/run/vdsm/sourceRoutes/$timeStamp
    fi
}
