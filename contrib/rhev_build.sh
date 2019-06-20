echo 'Please run this script from vdsm main folder'
echo '============================================'

make distclean
./autogen.sh \
        --system \
        --with-qemu-kvm='qemu-kvm-rhev' \
        --with-qemu-img='qemu-img-rhev' \
        --enable-hooks \
        --enable-vhostmd \
        --disable-python3
make dist

echo
echo 'Finish to compile VDSM for RHEV'
echo 'Use output tar and spec to continue'
