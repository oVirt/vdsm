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

SUBDIRS = sos storage

include $(top_srcdir)/build-aux/Makefile.subs

dist_vdsm_PYTHON = \
	betterThreading.py \
	caps.py \
	clientIF.py \
	configNetwork.py \
	define.py \
	dsaversion.py \
	guestIF.py \
	hooking.py \
	hooks.py \
	kaxmlrpclib.py \
	ksm.py \
	libvirtconnection.py \
	libvirtev.py \
	libvirtvm.py \
	logUtils.py \
	neterrors.py \
	netinfo.py \
	pthread.py \
	SecureXMLRPCServer.py \
	supervdsm.py \
	supervdsmServer.py \
	utils.py \
	vdsmDebugPlugin.py \
	vm.py

dist_vdsmexec_SCRIPTS = \
	ovirt_functions.sh \
	vdsm-gencerts.sh

nodist_vdsm_PYTHON = \
	config.py \
	constants.py

nodist_vdsm_SCRIPTS = \
	mk_sysprep_floppy \
	vdsm-restore-net-config \
	vdsm-store-net-config

nodist_noinst_DATA = \
	logger.conf \
	sudoers.vdsm \
	vdsm.conf.sample \
	vdsmd.init \
	vdsm.rwtab

dist_vdsm_SCRIPTS = \
	addNetwork \
	delNetwork \
	get-conf-item \
	get-vm-pid \
	prepare-vmchannel \
	respawn \
	set-conf-item \
	vdsm \
	write-net-config

nodist_man8_MANS = \
	vdsmd.8

CLEANFILES = \
	config.log \
	$(nodist_vdsm_PYTHON) \
	$(nodist_vdsm_SCRIPTS) \
	$(nodist_noinst_DATA) \
	$(nodist_man8_MANS)

EXTRA_DIST = \
	config.py.in \
	constants.py.in \
	libvirt_password \
	limits.conf \
	logger.conf.in \
	mk_sysprep_floppy.in \
	sudoers.vdsm.in \
	vdsmd.8.in \
	vdsmd.init.in \
	vdsmd.service \
	vdsm-gencerts.sh.in \
	vdsm-libvirt-access.pkla \
	vdsm-libvirt-logrotate \
	vdsm-logrotate \
	vdsm-logrotate.conf \
	vdsm-restore-net-config.in \
	vdsm.rwtab.in \
	vdsm-store-net-config.in

vdsm.conf.sample: Makefile config.py
	@echo "  CONFIG $@"; python $(builddir)/config.py > $@

all-local: \
	$(nodist_vdsm_PYTHON) \
	$(nodist_vdsm_SCRIPTS) \
	$(nodist_noinst_DATA) \
	$(nodist_man8_MANS)

install-data-hook:
	chmod 440 $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm
	chmod 775 $(DESTDIR)$(localstatedir)/lib/libvirt/qemu/channels

install-data-local: \
		install-data-logger \
		install-data-rwtab \
		install-data-logrotate \
		install-data-sudoers \
		install-data-libvirtpass
	$(MKDIR_P) $(DESTDIR)$(vdsmrepo)
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/keys
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/certs
	$(MKDIR_P) $(DESTDIR)$(vdsmlogdir)
	$(MKDIR_P) $(DESTDIR)$(vdsmrundir)
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/netconfback
	$(MKDIR_P) $(DESTDIR)$(vdsmpoolsdir)
	$(MKDIR_P) $(DESTDIR)$(vdsmbackupdir)
	$(MKDIR_P) $(DESTDIR)$(localstatedir)/lib/libvirt/qemu/channels

uninstall-local: \
		uninstall-data-logger \
		uninstall-data-rwtab \
		uninstall-data-logrotate \
		uninstall-data-sudoers \
		uninstall-data-libvirtpass

install-data-logger:
	$(MKDIR_P) $(DESTDIR)$(vdsmconfdir)
	$(INSTALL_DATA) logger.conf $(DESTDIR)$(vdsmconfdir)/logger.conf

uninstall-data-logger:
	$(RM) $(DESTDIR)$(vdsmconfdir)/logger.conf

install-data-libvirtpass:
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/keys
	$(INSTALL_DATA) -m 600 $(srcdir)/libvirt_password \
		$(DESTDIR)$(vdsmtsdir)/keys/libvirt_password

uninstall-data-libvirtpass:
	$(RM) $(DESTDIR)$(vdsmtsdir)/keys/libvirt_password

install-data-rwtab:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/rwtab.d
	$(INSTALL_DATA) vdsm.rwtab \
		$(DESTDIR)$(sysconfdir)/rwtab.d/vdsm

uninstall-data-rwtab:
	$(RM) $(DESTDIR)$(sysconfdir)/rwtab.d/vdsm

install-data-logrotate:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/logrotate.d
	$(INSTALL_DATA) $(srcdir)/vdsm-logrotate.conf \
		$(DESTDIR)$(sysconfdir)/logrotate.d/vdsm
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/cron.hourly
	$(INSTALL_SCRIPT) $(srcdir)/vdsm-logrotate \
		$(DESTDIR)$(sysconfdir)/cron.hourly/vdsm-logrotate
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/cron.d
	$(INSTALL_DATA) $(srcdir)/vdsm-libvirt-logrotate \
		$(DESTDIR)$(sysconfdir)/cron.d/vdsm-libvirt-logrotate

uninstall-data-logrotate:
	$(RM) $(DESTDIR)$(sysconfdir)/logrotate.d/vdsm
	$(RM) $(DESTDIR)$(sysconfdir)/cron.hourly/vdsm-logrotate
	$(RM) $(DESTDIR)$(sysconfdir)/cron.d/vdsm-libvirt-logrotate

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) sudoers.vdsm \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm

uninstall-data-sudoers:
	$(RM) $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm
