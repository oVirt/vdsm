# Install required packages and start services.
# This must run on all gluster nodes.  When done, you can create gluster
# bricks and volumes on one of the nodes.
#
# Requirements:
# - Fedora 29 VM
# - VirtIO OS disk

set -e

echo "Installing ovirt-release-master package"
dnf install -y http://resources.ovirt.org/pub/yum-repo/ovirt-release-master.rpm

echo "Installing runtime packages"
dnf install -y \
    kmod-kvdo \
    vdo \
    glusterfs-server

echo "Disabling firewalld"
systemctl stop firewalld
systemctl disable firewalld

echo "Starting gluster service"
systemctl enable glusterd --now
