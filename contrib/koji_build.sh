# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

echo 'Please run this script from vdsm main folder'
echo '============================================'

make distclean
./autogen.sh \
    --system \
    --disable-ovirt-vmconsole \
    --enable-vhostmd \
    --enable-hooks \
    --with-data-center='/run/vdsm/data-center'
make srpm

echo
echo 'Finish to compile VDSM for koji Fedora build'
echo 'Use output srp.rpm to import fedpkg'
