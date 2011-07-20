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

# Users and groups
AC_SUBST([VDSMUSER], [vdsm])
AC_SUBST([VDSMGROUP], [qemu])
AC_SUBST([METADATAGROUP], [kvm])
AC_SUBST([QEMUUSER], [qemu])

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

# VDSM registration default paths
AC_SUBST([vdsmregdir], ['${datarootdir}/vdsm-reg'])
AC_SUBST([vdsmregconfdir], ['${sysconfdir}/vdsm-reg'])
AC_SUBST([vdsmreglogdir], ['${localstatedir}/log/vdsm-reg'])

# External programs (sorted, please keep in order)
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
AC_PATH_PROG([FUSER_PATH], [fuser], [/sbin/fuser])
AC_PATH_PROG([GRUBBY_PATH], [grubby], [/sbin/grubby])
AC_PATH_PROG([HWCLOCK_PATH], [hwclock], [/usr/sbin/hwclock])
AC_PATH_PROG([IFCONFIG_PATH], [ifconfig], [/sbin/ifconfig])
AC_PATH_PROG([IFDOWN_PATH], [ifdown], [/sbin/ifdown])
AC_PATH_PROG([IFUP_PATH], [ifup], [/sbin/ifup])
AC_PATH_PROG([IONICE_PATH], [ionice], [/usr/bin/ionice])
AC_PATH_PROG([IPCALC_PATH], [ipcalc], [/bin/ipcalc])
AC_PATH_PROG([ISCSIADM_PATH], [iscsiadm], [/sbin/iscsiadm])
AC_PATH_PROG([KILLALL_PATH], [killall], [/usr/bin/killall])
AC_PATH_PROG([KILL_PATH], [kill], [/bin/kill])
AC_PATH_PROG([LSB_RELEASE_PATH], [lsb_release], [/usr/bin/lsb_release])
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
AC_PATH_PROG([SCSI_ID_PATH], [scsi_id], [/sbin/scsi_id])
AC_PATH_PROG([SED_PATH], [sed], [/bin/sed])
AC_PATH_PROG([SERVICE_PATH], [service], [/sbin/service])
AC_PATH_PROG([SETSID_PATH], [setsid], [/usr/bin/setsid])
AC_PATH_PROG([SHOWMOUNT_PATH], [showmount], [/usr/sbin/showmount])
AC_PATH_PROG([SUDO_PATH], [sudo], [/usr/bin/sudo])
AC_PATH_PROG([SU_PATH], [su], [/bin/su])
AC_PATH_PROG([TAR_PATH], [tar], [/bin/tar])
AC_PATH_PROG([TUNE2FS_PATH], [tune2fs], [/sbin/tune2fs])
AC_PATH_PROG([UMOUNT_PATH], [umount], [/bin/umount])
AC_PATH_PROG([UNAME_PATH], [uname], [/bin/uname])
AC_PATH_PROG([UNPERSIST_PATH], [unpersist], [/usr/sbin/unpersist])
AC_PATH_PROG([VCONFIG_PATH], [vconfig], [/sbin/vconfig])
AC_PATH_PROG([WGET_PATH], [wget], [/usr/bin/wget])
AC_PATH_PROG([YUM_PATH], [yum], [/usr/bin/yum])

AC_OUTPUT([
	Makefile
	vds_bootstrap/Makefile
	vdsm_cli/Makefile
	vdsm_hooks/faqemu/Makefile
	vdsm_hooks/Makefile
	vdsm_hooks/vhostmd/Makefile
	vdsm/Makefile
	vdsm_reg/Makefile
	vdsm/storage/Makefile
	vdsm/storage/protect/Makefile
	vdsm.spec
	vdsm/constants.py.in
	vdsm/sudoers.vdsm.in
	vdsm/mk_sysprep_floppy
	vdsm_reg/deployUtil.py
])
