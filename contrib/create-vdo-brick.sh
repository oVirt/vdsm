# Create brick backed by vdo device.
#
# Requirements:
# - Fedora 29 VM
# - 20G VirtIO disk with serial number $disk_serial (e.g. "gv0")
#   In virt-manager: VirtIO Disk 2 > Advanced options > Serial number.
#
# Usage:
#    create-vdo-brick.sh vdo-name disk-serial

set -e

vdo_name=${1:?vdo_name required}
disk_serial=${2:?disk_serial required}

vdo_device=/dev/mapper/$vdo_name
export_dir=/export/$vdo_name

echo "Creating vdo device"
# We can use 10x size of the real device for virtualization, but vdo uses 2.5G
# for itself for small disks.
vdo create --name=$vdo_name \
    --device=/dev/disk/by-id/virtio-$disk_serial \
    --vdoLogicalSize=100G \

echo "Creating filesystem"
mkfs.xfs -K $vdo_device

echo "Mounting filesystem"
mkdir -p $export_dir
mount $vdo_device $export_dir

echo "Adding filesystem to fstab"
mount_options="defaults,_netdev,x-systemd.device-timeout=0,x-systemd.requires=vdo.service"
echo "$vdo_device $export_dir xfs $mount_options 0 0" >> /etc/fstab

echo "Creating brick"
mkdir $export_dir/brick
