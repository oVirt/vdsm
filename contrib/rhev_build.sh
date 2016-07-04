echo 'Please run this script from vdsm main folder'
echo '============================================'

make distclean
./autogen.sh \
        --system \
        --with-smbios-manufacturer='Red Hat' \
        --with-smbios-osname='RHEV Hypervisor' \
        --with-qemu-kvm='qemu-kvm-rhev' \
        --with-qemu-img='qemu-img-rhev' \
        --enable-hooks \
        --enable-vhostmd-hook \
        --disable-python3 \
        --disable-openvswitch
make dist

echo
echo 'Finish to compile VDSM for RHEV'
echo 'Use output tar and spec to continue'
