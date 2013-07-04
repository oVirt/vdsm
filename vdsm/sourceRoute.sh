#!/bin/bash

timeStamp=`date +%s`

sourceRoute_config() {
    echo "configure" "$new_ip_address" "$new_subnet_mask" "$new_routers" \
        "$interface" > /var/run/vdsm/sourceRoutes/$timeStamp
}

sourceRoute_restore() {
    echo "remove" "$interface" > \
        /var/run/vdsm/sourceRoutes/$timeStamp
}
