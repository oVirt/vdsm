#!/bin/bash

sourceRoute_config() {
    python /usr/share/vdsm/sourceRoute.pyc "configure" "dhcp" $new_ip_address \
        $new_subnet_mask $new_routers $interface
}

sourceRoute_restore() {
    python /usr/share/vdsm/sourceRoute.pyc "remove" "dhcp" $interface
}
