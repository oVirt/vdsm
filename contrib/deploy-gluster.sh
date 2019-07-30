# Install gluster and configure storage on a node.
# This must run on all gluster nodes.
# When done, you can create gluster volumes on one of the nodes.
#
# Requirements:
# - Fedor 29 VM
# - VirtIO OS disk
# - 20G VirtIO disk for gluster volume with serial number "gv0".
#   In virt-manager: VirtIO Disk 2 > Advanced options > Serial number.

set -e

vdo_name=vdo0

echo "Installing ovirt-release-master package"
dnf install -y http://resources.ovirt.org/pub/yum-repo/ovirt-release-master.rpm

echo "Installing runtime packages"
dnf install -y \
    kmod-kvdo \
    vdo \
    glusterfs-server

echo "Creating vdo device"
# We can use 10x size of the real device for virtualization, but vdo uses 2.5G
# for itself for small disks.
vdo create --name=$vdo_name \
    --device=/dev/disk/by-id/virtio-gv0 \
    --vdoLogicalSize=100G \

echo "Creating filesystem"
mkfs.xfs -K /dev/mapper/$vdo_name

echo "Mounting filesystem"
mkdir -p /export/$vdo_name
mount /dev/mapper/$vdo_name /export/$vdo_name

echo "Adding filesystem to fstab"
echo "/dev/mapper/$vdo_name /export/$vdo_name xfs defaults,_netdev,x-systemd.device-timeout=0,x-systemd.requires=vdo.service 0 0" >> /etc/fstab

echo "Creating brick"
mkdir /export/$vdo_name/brick

echo "Disabling firewalld"
systemctl stop firewalld
systemctl disable firewalld

echo "Starting gluster service"
systemctl enable glusterd --now
