# oVirt CI helper functions

create_loop_devices() {
    local last=$(($1-1))
    local min
    for min in `seq 0 $last`; do
        local name=/dev/loop$min
        if [ ! -e "$name" ]; then
            mknod --mode 0666 $name b 7 $min
        fi
    done
}
