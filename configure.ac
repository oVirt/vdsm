#
# Copyright 2008-2019 Red Hat, Inc.
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
        [m4_esyscmd([build-aux/pkg-version --version])],
        [devel@ovirt.org])
AC_CONFIG_AUX_DIR([build-aux])
AC_CANONICAL_HOST

m4_include([m4/ax_python_module.m4])

# Package release
AC_ARG_ENABLE(
    [timestamp],
    [AS_HELP_STRING(
        [--enable-timestamp],
        [add a timestamp to the package release @<:@default=no@:>@]
    )],
    ,
    [enable_timestamp="no"]
)
AS_IF([test "${enable_timestamp}" = "yes"],
      AC_SUBST([PACKAGE_RELEASE],
               [m4_esyscmd([build-aux/pkg-version --timestamp-release])]),
      AC_SUBST([PACKAGE_RELEASE],
               [m4_esyscmd([build-aux/pkg-version --release])]))

# Testing for version and release
AS_IF([test "x$PACKAGE_VERSION" = x],
      AC_MSG_ERROR([package version not defined]))
AS_IF([test "x$PACKAGE_RELEASE" = x],
      AC_MSG_ERROR([package release not defined]))

AC_SUBST([PACKAGE_BASE_RELEASE],
         [$(echo "$PACKAGE_RELEASE" | sed 's/\([0-9]\+\(\.[0-9]\+\)\?\).*/\1/')])

AC_SUBST([PACKAGE_BASE_VERSION],
         [$(echo "$PACKAGE_VERSION" | sed 's/\([0-9]\+\(\.[0-9]\+\)\?\).*/\1/')])

# Automake initialization
AM_INIT_AUTOMAKE([-Wno-portability])

# Checking for build tools
AC_PROG_LN_S

AM_PATH_PYTHON([3])

AC_ARG_ENABLE(
    [hooks],
    [AS_HELP_STRING(
        [--enable-hooks],
        [build hooks packages @<:@default=no@:>@]
    )],
    ,
    [enable_hooks="no"]
)
AM_CONDITIONAL([HOOKS], [test "${enable_hooks}" = "yes"])
AS_IF([test "${enable_hooks}" = "yes"],
      AC_SUBST([HOOKS], ['1']),
      AC_SUBST([HOOKS], ['0']))

AC_ARG_ENABLE(
    [vhostmd],
    [AS_HELP_STRING(
        [--enable-vhostmd],
        [build vdsm-vhostmd-hook subpackage @<:@default=no@:>@]
    )],
    ,
    [enable_vhostmd="no"]
)
AS_IF([test "${enable_vhostmd}" = "yes"],
      AC_SUBST([VHOSTMD], ['1']),
      AC_SUBST([VHOSTMD], ['0']))

AC_ARG_ENABLE(
    [gluster-mgmt],
    [AS_HELP_STRING(
        [--enable-gluster-mgmt],
        [build gluster management package @<:@default=yes@:>@]
    )],
    ,
    [enable_gluster_mgmt="yes"]
)
AM_CONDITIONAL([GLUSTER_MGMT], [test "${enable_gluster_mgmt}" = "yes"])
AS_IF([test "${enable_gluster_mgmt}" = "yes"],
      AC_SUBST([GLUSTER_MGMT], ['1']),
      AC_SUBST([GLUSTER_MGMT], ['0']))

AC_ARG_ENABLE(
    [ovirt-vmconsole],
    [AS_HELP_STRING(
        [--disable-ovirt-vmconsole],
        [disable ovirt-vmconsole integration @<:@default=no@:>@]
    )],
    ,
    [enable_ovirt_vmconsole="yes"]
)
AS_IF([test "${enable_ovirt_vmconsole}" = "yes"],
      AC_SUBST([OVIRT_VMCONSOLE], ['1']),
      AC_SUBST([OVIRT_VMCONSOLE], ['0']))

AC_ARG_ENABLE(
    [libvirt-sanlock],
    [AS_HELP_STRING(
        [--disable-libvirt-sanlock],
        [disable libvirt sanlock plugin]
    )],
    ,
    [enable_libvirt_sanlock="yes"]
)
AC_SUBST([ENABLE_LIBVIRT_SANLOCK], ["${enable_libvirt_sanlock}"])

AC_ARG_ENABLE(
    [libvirt-selinux],
    [AS_HELP_STRING(
        [--disable-libvirt-selinux],
        [disable libvirt selinux plugin]
    )],
    ,
    [enable_libvirt_selinux="yes"]
)
AC_SUBST([ENABLE_LIBVIRT_SELINUX], ["${enable_libvirt_selinux}"])

AC_ARG_ENABLE(
    [openvswitch],
    [AS_HELP_STRING(
        [--enable-openvswitch],
        [enable openvswitch usage @<:@default=yes@:>@]
    )],
    ,
    [enable_openvswitch="yes"]
)
AS_IF([test "${enable_openvswitch}" = "yes"],
      AC_SUBST([OPENVSWITCH], ['1']),
      AC_SUBST([OPENVSWITCH], ['0']))

AS_IF([test "$host_cpu" = powerpc64le],
      AC_SUBST([OPENVSWITCH], ['0']),)

AS_IF([test "${enable_openvswitch}" = "yes"],
      AC_SUBST([OPENVSWITCHSERVICE], ['openvswitch.service']),
      AC_SUBST([OPENVSWITCHSERVICE], []))

AC_ARG_WITH(
    [qemu-user],
    [AS_HELP_STRING(
        [--with-qemu-user=USER],
        [configure user of qemu process spawned by libvirt, @<:@default=qemu@:>@]
    )],
    ,
    [with_qemu_user="qemu"]
)
AC_SUBST([QEMUUSER], ["${with_qemu_user}"])

AC_ARG_WITH(
    [qemu-group],
    [AS_HELP_STRING(
        [--with-qemu-group=GROUP],
        [configure group of qemu process spawned by libvirt, @<:@default=qemu@:>@]
    )],
    ,
    [with_qemu_group="qemu"]
)
AC_SUBST([QEMUGROUP], ["${with_qemu_group}"])

AC_ARG_WITH(
    [ovirt-vmconsole-user],
    [AS_HELP_STRING(
        [--with-ovirt-vmconsole-user=USER],
        [configure user for ovirt vmconsole access, @<:@default=ovirt-vmconsole@:>@]
    )],
    ,
    [with_ovirt_vmconsole_user="ovirt-vmconsole"]
)
AC_SUBST([OVIRTVMCONSOLEUSER], ["${with_ovirt_vmconsole_user}"])

AC_ARG_WITH(
    [ovirt-vmconsole-group],
    [AS_HELP_STRING(
        [--with-ovirt-vmconsole-group=GROUP],
        [configure group for ovirt vmconsole access, @<:@default=ovirt-vmconsole@:>@]
    )],
    ,
    [with_ovirt_vmconsole_group="ovirt-vmconsole"]
)
AC_SUBST([OVIRTVMCONSOLEGROUP], ["${with_ovirt_vmconsole_group}"])

AC_ARG_WITH(
    [libvirt-service-default],
    [AS_HELP_STRING(
        [--with-libvirt-service-default=FILE],
        [configure full path of libvirt service environment file.
         @<:@SYSCONFDIR/sysconfig/libvirtd@:>@]
    )],
    ,
    [with_libvirt_service_default="${sysconfdir}/sysconfig/libvirtd"]
)
AC_SUBST([LIBVIRT_SERVICE_DEFAULT], ["${with_libvirt_service_default}"])

AC_ARG_WITH(
    [data-center],
    [AS_HELP_STRING([--with-data-center=FILE],
                    [configure full path of ovirt data center, @<:@default=/rhev/data-center@:>@])],
    [with_data_center="${withval}"],
    [with_data_center="/rhev/data-center"])
AC_SUBST([vdsmrepo], [${with_data_center}])

if grep -q 'Fedora' /etc/redhat-release; then
    fedora_run="yes"
else
    fedora_run="no"
fi

AS_IF([test "${fedora_run}" = "yes"],
    [_polkitdir="/usr/share/polkit-1/rules.d"],
    [_polkitdir="/var/lib/polkit-1/localauthority/10-vendor.d"])

AC_ARG_WITH(
    [polkitdir],
    [AS_HELP_STRING([--with-polkitdir=FILE],
                    [where to place polkit rules, @<:@default=/usr/share/polkit-1/rules.d@:>@])],
    [with_polkitdir="${withval}"],
    [with_polkitdir="${_polkitdir}"])
AC_SUBST([polkitdir], [${with_polkitdir}])

# Users and groups
AC_SUBST([VDSMUSER], [vdsm])
AC_SUBST([VDSMGROUP], [kvm])
AC_SUBST([METADATAUSER], [vdsm])
AC_SUBST([METADATAGROUP], [kvm])
AC_SUBST([SNLKUSER], [sanlock])
AC_SUBST([SNLKGROUP], [sanlock])
AC_SUBST([CDROMGROUP], [cdrom])

# Systemd units
AC_SUBST([SYSTEMD_UNIT_DIR], ['${prefix}/lib/systemd/system'])
AS_IF([test "$host_cpu" = powerpc64le],
      AC_SUBST([SYSTEMD_LIMIT_MEMLOCK], [LimitMEMLOCK=64M]),
      AC_SUBST([SYSTEMD_LIMIT_MEMLOCK], []))

# VDSM default paths
AC_SUBST([vdsmdir], ['${datarootdir}/vdsm'])
AC_SUBST([vdsmconfdir], ['${sysconfdir}/vdsm'])
AC_SUBST([vdsmlogdir], ['${localstatedir}/log/vdsm'])
AC_SUBST([vdsmrundir], ['${localstatedir}/run/vdsm'])
AC_SUBST([vdsmlibdir], ['${localstatedir}/lib/vdsm'])
AC_SUBST([vdsmbackupdir], ['${vdsmlogdir}/backup'])
AC_SUBST([vdsmexecdir], ['${libexecdir}/vdsm'])
AC_SUBST([vdsmhooksdir], ['${vdsmexecdir}/hooks'])
AC_SUBST([vdsmtsdir], ['${sysconfdir}/pki/vdsm'])
AC_SUBST([vdsmpylibdir], ['${pythondir}/vdsm'])
AC_SUBST([vdsmnetinfodir], ['${vdsmpylibdir}/network/netinfo'])
AC_SUBST([vdsmtooldir], ['${vdsmpylibdir}/tool'])
AC_SUBST([configuratorsdir], ['${vdsmtooldir}/configurators'])
AC_SUBST([vdsmtestsdir], ['${datarootdir}/vdsm/tests'])
AC_SUBST([vdsmtransientdisksdir], ['${vdsmlibdir}/storage/transient_disks'])
AC_SUBST([vdsmrunbackupdir], ['${vdsmrundir}/backup'])


# VDSM service
AC_SUBST([SERVICE_STOP_TIMEOUT], ['10'])

AC_SUBST([VMCON_USER],ovirt-vmconsole)
AC_SUBST([VMCON_GROUP],ovirt-vmconsole)

configure_flags="--with-data-center=$vdsmrepo\
 --with-polkitdir=$polkitdir"
if test "$enable_timestamp" == "yes"; then
    configure_flags+=" --enable-timestamp"
fi
if test "$HOOKS" == "1"; then
    configure_flags+=" --enable-hooks"
fi
if test "$GLUSTER_MGMT" == "0"; then
    configure_flags+=" --disable-gluster-mgmt"
fi
if test "$OPENVSWITCH" == "0"; then
    configure_flags+=" --disable-openvswitch"
fi
if test "$OVIRT_VMCONSOLE" == "1"; then
    configure_flags+=" --with-ovirt-vmconsole-user=$VMCON_USER\
     --with-ovirt-vmconsole-group=$VMCON_GROUP"
else
    configure_flags+=" --with-ovirt-vmconsole-user=$VDSMUSER\
     --with-ovirt-vmconsole-group=$VDSMGROUP"
fi

AC_SUBST([CONFIGURE_FLAGS], ["$configure_flags"])

AX_PYTHON_MODULE([six], [fatal], python3)

AC_PATH_PROG([PYTHON_CONFIG], ["python3-config"])
if test "x$PYTHON_CONFIG" = "x"; then
  AC_MSG_ERROR([python3-devel not found, please install it.])
fi

# External programs (sorted, please keep in order)
AC_PATH_PROG([CAT_PATH], [cat], [/bin/cat])
AC_PATH_PROG([CHCON_PATH], [chcon], [/bin/chcon])
AC_PATH_PROG([CHMOD_PATH], [chmod], [/bin/chmod])
AC_PATH_PROG([CHOWN_PATH], [chown], [/bin/chown])
AC_PATH_PROG([DD_PATH], [dd], [/bin/dd])
AC_PATH_PROG([DMSETUP_PATH], [dmsetup], [/sbin/dmsetup])
AC_PATH_PROG([FSCK_PATH], [fsck], [/sbin/fsck])
AC_PATH_PROG([FENCE_AGENT_PATH], [fence_ilo], [/usr/sbin/fence_ilo])
AC_PATH_PROG([FUSER_PATH], [fuser], [/sbin/fuser])
AC_PATH_PROG([GREP_PATH], [grep], [/bin/grep])
AC_PATH_PROG([HWCLOCK_PATH], [hwclock], [/usr/sbin/hwclock])
AC_PATH_PROG([IONICE_PATH], [ionice], [/usr/bin/ionice])
AC_PATH_PROG([IP_PATH], [ip], [/sbin/ip])
AC_PATH_PROG([ISCSIADM_PATH], [iscsiadm], [/sbin/iscsiadm])
AC_PATH_PROG([KILL_PATH], [kill], [/bin/kill])
AC_PATH_PROG([LVM_PATH], [lvm], [/sbin/lvm])
AC_PATH_PROG([MKFS_MSDOS_PATH], [mkfs.msdos], [/sbin/mkfs.msdos])
AC_PATH_PROG([MKFS_PATH], [mkfs], [/sbin/mkfs])
AC_PATH_PROG([MKISOFS_PATH], [mkisofs], [/usr/bin/mkisofs])
AC_PATH_PROG([MOUNTPOINT_PATH], [mountpoint], [/bin/mountpoint])
AC_PATH_PROG([MOUNT_PATH], [mount], [/bin/mount])
AC_PATH_PROG([MULTIPATH_PATH], [multipath], [/sbin/multipath])
AC_PATH_PROG([MV_PATH], [mv], [/bin/mv])
AC_PATH_PROG([NICE_PATH], [nice], [/bin/nice])
AC_PATH_PROG([NTPQ_PATH], [ntpq], [/usr/sbin/ntpq])
AC_PATH_PROG([OPENSSL_PATH], [openssl], [/usr/bin/openssl])
AC_PATH_PROG([PRLIMIT_PATH], [prlimit], [/usr/bin/prlimit])
AC_PATH_PROG([QEMUIMG_PATH], [qemu-img], [/usr/bin/qemu-img])
AC_PATH_PROG([REBOOT_PATH], [reboot], [/usr/bin/reboot])
AC_PATH_PROG([RESTORECON_PATH], [restorecon], [/sbin/restorecon])
AC_PATH_PROG([RM_PATH], [rm], [/bin/rm])
AC_PATH_PROG([RSYNC_PATH], [rsync], [/usr/bin/rsync])
AC_PATH_PROG([SAFELEASE_PATH], [safelease], [/usr/libexec/safelease/safelease])
AC_PATH_PROG([SERVICE_PATH], [service], [/sbin/service])
AC_PATH_PROG([SETSID_PATH], [setsid], [/usr/bin/setsid])
AC_PATH_PROG([SUDO_PATH], [sudo], [/usr/bin/sudo])
AC_PATH_PROG([SU_PATH], [su], [/bin/su])
AC_PATH_PROG([SYSCTL_PATH], [sysctl], [/sbin/sysctl])
AC_PATH_PROG([TAR_PATH], [tar], [/bin/tar])
AC_PATH_PROG([TASKSET_PATH], [taskset], [/usr/bin/taskset])
AC_PATH_PROG([TEE_PATH], [tee], [/usr/bin/tee])
AC_PATH_PROG([TOUCH_PATH], [touch], [/bin/touch])
AC_PATH_PROG([TUNE2FS_PATH], [tune2fs], [/sbin/tune2fs])
AC_PATH_PROG([UMOUNT_PATH], [umount], [/bin/umount])
AC_PATH_PROG([WGET_PATH], [wget], [/usr/bin/wget])

# Keep sorted
AC_OUTPUT([
	Makefile
	contrib/Makefile
	helpers/Makefile
	init/Makefile
	init/systemd/Makefile
	lib/Makefile
        lib/dnf-plugins/Makefile
	lib/vdsm/Makefile
	lib/vdsm/api/Makefile
	lib/vdsm/common/Makefile
	lib/vdsm/common/network/Makefile
	lib/vdsm/gluster/Makefile
	lib/vdsm/hook/Makefile
	lib/vdsm/host/Makefile
	lib/vdsm/metrics/Makefile
	lib/vdsm/network/Makefile
	lib/vdsm/network/configurators/Makefile
	lib/vdsm/network/ip/Makefile
	lib/vdsm/network/ip/address/Makefile
	lib/vdsm/network/ip/route/Makefile
	lib/vdsm/network/ip/rule/Makefile
	lib/vdsm/network/link/Makefile
	lib/vdsm/network/link/bond/Makefile
	lib/vdsm/network/lldp/Makefile
	lib/vdsm/network/lldpad/Makefile
	lib/vdsm/network/netinfo/Makefile
	lib/vdsm/network/netlink/Makefile
	lib/vdsm/network/netswitch/Makefile
	lib/vdsm/network/nm/Makefile
	lib/vdsm/network/nmstate/Makefile
	lib/vdsm/network/nm/nmdbus/Makefile
	lib/vdsm/network/ovs/Makefile
	lib/vdsm/network/ovs/driver/Makefile
	lib/vdsm/network/tc/Makefile
	lib/vdsm/profiling/Makefile
	lib/vdsm/rpc/Makefile
	lib/vdsm/storage/Makefile
	lib/vdsm/storage/protect/Makefile
	lib/vdsm/storage/sdm/Makefile
	lib/vdsm/storage/sdm/api/Makefile
	lib/vdsm/storage/vdsm_lvm_rules.template
	lib/vdsm/supervdsm_api/Makefile
	lib/vdsm/tool/Makefile
	lib/vdsm/tool/configurators/Makefile
	lib/vdsm/virt/Makefile
	lib/vdsm/virt/jobs/Makefile
	lib/vdsm/virt/libvirthook/Makefile
	lib/vdsm/virt/vmdevices/Makefile
	lib/vdsmclient/Makefile
	lib/yajsonrpc/Makefile
	static/Makefile
	tests/Makefile
	vdsm_hooks/Makefile
	vdsm_hooks/allocate_net/Makefile
	vdsm_hooks/boot_hostdev/Makefile
	vdsm_hooks/checkimages/Makefile
	vdsm_hooks/checkips/Makefile
	vdsm_hooks/cpuflags/Makefile
	vdsm_hooks/diskunmap/Makefile
	vdsm_hooks/ethtool_options/Makefile
	vdsm_hooks/extnet/Makefile
	vdsm_hooks/extra_ipv4_addrs/Makefile
	vdsm_hooks/fakevmstats/Makefile
	vdsm_hooks/faqemu/Makefile
	vdsm_hooks/fcoe/Makefile
	vdsm_hooks/fileinject/Makefile
	vdsm_hooks/httpsisoboot/Makefile
	vdsm_hooks/localdisk/Makefile
	vdsm_hooks/macbind/Makefile
	vdsm_hooks/nestedvt/Makefile
	vdsm_hooks/openstacknet/Makefile
	vdsm_hooks/qemucmdline/Makefile
	vdsm_hooks/scratchpad/Makefile
	vdsm_hooks/smbios/Makefile
	vdsm_hooks/spiceoptions/Makefile
	vdsm_hooks/vhostmd/Makefile
	vdsm_hooks/vmfex_dev/Makefile
])
