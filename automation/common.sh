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

create_artifacts_repo() {
    local repo="$1"

    createrepo "$repo"

    # Some slaves have /etc/dnf/dnf.conf when running el7 build - patch both
    # yum.conf and dnf.conf to make sure our repo is found.
    local url="file://$repo"
    for conf in /etc/yum.conf /etc/dnf/dnf.conf; do
        if [ -f "$conf" ]; then
            cat automation/artifacts.repo | sed -e "s#@BASEURL@#$url#" >> "$conf"
        fi
    done
}
