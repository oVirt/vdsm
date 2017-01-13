#!/bin/bash
set -e

shopt -s extglob
IFACES=(/proc/sys/net/ipv4/conf/!(all|default|lo))
shopt -u extglob

DIST=$(uname -r | sed -r  's/^.*\.([^\.]+)\.[^\.]+$/\1/')
ADDR=''

for iface in "${IFACES[@]}"; do
    ADDR="$( \
        /sbin/ip -4 -o addr show dev "${iface##*/}" \
        | awk '{split($4,a,"."); print a[1] "." a[2] "." a[3] ".1"}' \
    )"
    if [[ "$ADDR" != "" ]]; then
        break
    fi
done

if [[ "$ADDR" == "" ]]; then
    echo "Failed to detect ip address"
    exit 1
fi


# Fix for bz:1195882
yum install --nogpgcheck -y libvirt-daemon
rm -rf /var/cache/libvirt/qemu/capabilities
systemctl restart libvirtd.service || :

# enable the local repo, cost=1 to keep it in high priority
cat > /etc/yum.repos.d/local-ovirt.repo <<EOF
[localsync]
name=VDSM artifacts
baseurl=http://$ADDR:8585/$DIST/
enabled=1
skip_if_unavailable=0
gpgcheck=0
cost=1
proxy=_none_
EOF

echo "######################### Cleaning up caches"
yum clean all
echo "######################### Installing vdsm"
yum install --nogpgcheck -y vdsm vdsm-cli vdsm-tests python-pip
yum install -y python-mock
pip install nose-timer
echo "######################### Configuring vdsm"
vdsm-tool configure --force
echo "######################### Starting up vdsm"
systemctl start vdsmd
echo "@@@@@@@@@@@@@@@@@@@@ DONE"
