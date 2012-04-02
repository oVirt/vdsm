#
# Copyright 2008-2012 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

# Autoconf initialization
AC_INIT([vdsm],
        [m4_esyscmd([build-aux/version.sh])],
        [vdsm-devel@lists.fedorahosted.org])
AC_CONFIG_AUX_DIR([build-aux])

# Package release
AC_SUBST([PACKAGE_RELEASE],
         [m4_esyscmd([build-aux/release.sh])])

# Testing for version and release
AS_IF([test "x$PACKAGE_VERSION" = x],
      AC_MSG_ERROR([package version not defined]))
AS_IF([test "x$PACKAGE_RELEASE" = x],
      AC_MSG_ERROR([package release not defined]))

# Automake initialization
AM_INIT_AUTOMAKE([-Wno-portability])

# Checking for build tools
AC_PROG_CC
AC_PROG_LN_S
AM_PATH_PYTHON([2.6])

# Checking if hooks enables
AC_ARG_ENABLE([hooks],
[  --enable-hooks    build hooks RPMs],
[case "${enableval}" in
  yes) hooks=true ;;
  no)  hooks=false ;;
  *) AC_MSG_ERROR([bad value ${enableval} for --enable-hooks]) ;;
esac],[hooks=false])
AM_CONDITIONAL([HOOKS], [test x$hooks = xtrue])

# Users and groups
AC_SUBST([VDSMUSER], [vdsm])
AC_SUBST([VDSMGROUP], [kvm])
AC_SUBST([METADATAUSER], [vdsm])
AC_SUBST([METADATAGROUP], [kvm])
AC_SUBST([QEMUUSER], [qemu])
AC_SUBST([QEMUGROUP], [qemu])
AC_SUBST([SNLKUSER], [sanlock])
AC_SUBST([SNLKGROUP], [sanlock])

# VDSM default paths
AC_SUBST([vdsmdir], ['${datarootdir}/vdsm'])
AC_SUBST([vdsmconfdir], ['${sysconfdir}/vdsm'])
AC_SUBST([vdsmlogdir], ['${localstatedir}/log/vdsm'])
AC_SUBST([vdsmrundir], ['${localstatedir}/run/vdsm'])
AC_SUBST([vdsmlibdir], ['${localstatedir}/lib/vdsm'])
AC_SUBST([vdsmpoolsdir], ['${vdsmrundir}/pools'])
AC_SUBST([vdsmbackupdir], ['${vdsmlogdir}/backup'])
AC_SUBST([vdsmexecdir], ['${libexecdir}/vdsm'])
AC_SUBST([vdsmhooksdir], ['${vdsmexecdir}/hooks'])
AC_SUBST([vdsmtsdir], ['${sysconfdir}/pki/vdsm'])
AC_SUBST([vdsmrepo], ['/rhev/data-center'])
AC_SUBST([vdsmpylibdir], ['${pythondir}/vdsm'])
AC_SUBST([vdsmtooldir], ['${vdsmpylibdir}/tool'])

# VDSM registration default paths
AC_SUBST([vdsmregdir], ['${datarootdir}/vdsm-reg'])
AC_SUBST([vdsmbootstrapdir], ['${datarootdir}/vdsm-bootstrap'])
AC_SUBST([vdsmregconfdir], ['${sysconfdir}/vdsm-reg'])
AC_SUBST([vdsmreglogdir], ['${localstatedir}/log/vdsm-reg'])

# Network
AC_SUBST([VDSMBRIDGE], ['ovirtmgmt'])

# Checking for pyflakes
AC_PATH_PROG([PYFLAKES], [pyflakes])
if test "x$PYFLAKES" = "x"; then
  AC_MSG_ERROR([pyflakes not found, please install it.])
fi

# External programs (sorted, please keep in order)
AC_PATH_PROG([BLKID_PATH], [blkid], [/sbin/blkid])
AC_PATH_PROG([BLOCKDEV_PATH], [blockdev], [/sbin/blockdev])
AC_PATH_PROG([BRCTL_PATH], [brctl], [/usr/sbin/brctl])
AC_PATH_PROG([CAT_PATH], [cat], [/bin/cat])
AC_PATH_PROG([CHKCONFIG_PATH], [chkconfig], [/sbin/chkconfig])
AC_PATH_PROG([CHOWN_PATH], [chown], [/bin/chown])
AC_PATH_PROG([CP_PATH], [cp], [/bin/cp])
AC_PATH_PROG([DD_PATH], [dd], [/bin/dd])
AC_PATH_PROG([DMIDECODE_PATH], [dmidecode], [/usr/sbin/dmidecode])
AC_PATH_PROG([DMSETUP_PATH], [dmsetup], [/sbin/dmsetup])
AC_PATH_PROG([ECHO_PATH], [echo], [/bin/echo])
AC_PATH_PROG([FSCK_PATH], [fsck], [/sbin/fsck])
AC_PATH_PROG([FENCE_AGENT_PATH], [fence_ilo], [/usr/sbin/fence_ilo])
AC_PATH_PROG([FUSER_PATH], [fuser], [/sbin/fuser])
AC_PATH_PROG([GRUBBY_PATH], [grubby], [/sbin/grubby])
AC_PATH_PROG([HWCLOCK_PATH], [hwclock], [/usr/sbin/hwclock])
AC_PATH_PROG([IFCONFIG_PATH], [ifconfig], [/sbin/ifconfig])
AC_PATH_PROG([IFDOWN_PATH], [ifdown], [/sbin/ifdown])
AC_PATH_PROG([IFUP_PATH], [ifup], [/sbin/ifup])
AC_PATH_PROG([IONICE_PATH], [ionice], [/usr/bin/ionice])
AC_PATH_PROG([IPCALC_PATH], [ipcalc], [/bin/ipcalc])
AC_PATH_PROG([ISCSIADM_PATH], [iscsiadm], [/sbin/iscsiadm])
AC_PATH_PROG([KILL_PATH], [kill], [/bin/kill])
AC_PATH_PROG([LSBLK_PATH], [lsblk], [/bin/lsblk])
AC_PATH_PROG([LVM_PATH], [lvm], [/sbin/lvm])
AC_PATH_PROG([MKFS_PATH], [mkfs], [/sbin/mkfs])
AC_PATH_PROG([MOUNT_PATH], [mount], [/bin/mount])
AC_PATH_PROG([MULTIPATH_PATH], [multipath], [/sbin/multipath])
AC_PATH_PROG([MV_PATH], [mv], [/bin/mv])
AC_PATH_PROG([NICE_PATH], [nice], [/bin/nice])
AC_PATH_PROG([OPENSSL_PATH], [openssl], [/usr/bin/openssl])
AC_PATH_PROG([PERSIST_PATH], [persist], [/usr/sbin/persist])
AC_PATH_PROG([PGREP_PATH], [pgrep], [/usr/bin/pgrep])
AC_PATH_PROG([PYTHON_PATH], [python], [/usr/bin/python])
AC_PATH_PROG([QEMUIMG_PATH], [qemu-img], [/usr/bin/qemu-img])
AC_PATH_PROG([REBOOT_PATH], [reboot], [/usr/bin/reboot])
AC_PATH_PROG([RPM_PATH], [rpm], [/bin/rpm])
AC_PATH_PROG([RSYNC_PATH], [rsync], [/usr/bin/rsync])
AC_PATH_PROG([SCSI_ID_PATH], [scsi_id], [/sbin/scsi_id],
             [$PATH$PATH_SEPARATOR/lib/udev])
AC_PATH_PROG([SED_PATH], [sed], [/bin/sed])
AC_PATH_PROG([SERVICE_PATH], [service], [/sbin/service])
AC_PATH_PROG([SETSID_PATH], [setsid], [/usr/bin/setsid])
AC_PATH_PROG([SHOWMOUNT_PATH], [showmount], [/usr/sbin/showmount])
AC_PATH_PROG([SUDO_PATH], [sudo], [/usr/bin/sudo])
AC_PATH_PROG([SU_PATH], [su], [/bin/su])
AC_PATH_PROG([SYSTEMCTL_PATH], [systemctl], [/bin/systemctl])
AC_PATH_PROG([TAR_PATH], [tar], [/bin/tar])
AC_PATH_PROG([TC_PATH], [tc], [/sbin/tc])
AC_PATH_PROG([TUNE2FS_PATH], [tune2fs], [/sbin/tune2fs])
AC_PATH_PROG([UDEVADM_PATH], [udevadm], [/sbin/udevadm])
AC_PATH_PROG([UMOUNT_PATH], [umount], [/bin/umount])
AC_PATH_PROG([UNAME_PATH], [uname], [/bin/uname])
AC_PATH_PROG([UNPERSIST_PATH], [unpersist], [/usr/sbin/unpersist])
AC_PATH_PROG([VCONFIG_PATH], [vconfig], [/sbin/vconfig])
AC_PATH_PROG([WGET_PATH], [wget], [/usr/bin/wget])
AC_PATH_PROG([YUM_PATH], [yum], [/usr/bin/yum])

# Keep sorted
AC_OUTPUT([
	Makefile
	tests/Makefile
	vds_bootstrap/Makefile
	vdsm_cli/Makefile
	vdsm_hooks/directlun/Makefile
	vdsm_hooks/faqemu/Makefile
	vdsm_hooks/fileinject/Makefile
	vdsm_hooks/floppy/Makefile
	vdsm_hooks/hostusb/Makefile
	vdsm_hooks/hugepages/Makefile
	vdsm_hooks/isolatedprivatevlan/Makefile
	vdsm_hooks/Makefile
	vdsm_hooks/numa/Makefile
	vdsm_hooks/pincpu/Makefile
	vdsm_hooks/promisc/Makefile
	vdsm_hooks/qos/Makefile
	vdsm_hooks/scratchpad/Makefile
	vdsm_hooks/smartcard/Makefile
	vdsm_hooks/smbios/Makefile
	vdsm_hooks/sriov/Makefile
	vdsm_hooks/vhostmd/Makefile
	vdsm_hooks/vmdisk/Makefile
	vdsm/Makefile
	vdsm_reg/Makefile
	vdsm/sos/Makefile
	vdsm/storage/Makefile
	vdsm/storage/protect/Makefile
	vdsm-tool/Makefile
])
AC_OUTPUT([tests/run_tests.sh], [chmod +x tests/run_tests.sh])

