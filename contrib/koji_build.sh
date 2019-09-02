echo 'Please run this script from vdsm main folder'
echo '============================================'

make distclean
./autogen.sh \
    --system \
    --disable-ovirt-imageio \
    --disable-ovirt-vmconsole \
    --enable-vhostmd \
    --enable-hooks \
    --enable-4k-support \
    --with-data-center='/run/vdsm/data-center'
make srpm

echo
echo 'Finish to compile VDSM for koji Fedora build'
echo 'Use output srp.rpm to import fedpkg'
