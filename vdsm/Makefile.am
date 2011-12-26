# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

SUBDIRS = storage

dist_vdsm_DATA = \
	betterThreading.py \
	caps.py \
	clientIF.py \
	configNetwork.py \
	config.py \
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
	ovirt_functions.sh

nodist_vdsm_DATA = \
	config.py \
	constants.py

nodist_vdsm_SCRIPTS = \
	vdsm-restore-net-config \
	vdsm-store-net-config

vdsm_data_SUBST = \
	logger.conf \
	sudoers.vdsm \
	vdsm.rwtab \
	vdsmd.init \
	vdsm-sosplugin.py

nodist_noinst_DATA = \
	$(vdsm_data_SUBST) \
	vdsm.conf.sample

dist_vdsm_SCRIPTS = \
	addNetwork \
	delNetwork \
	get-conf-item \
	get-vm-pid \
	mk_sysprep_floppy \
	prepare-vmchannel \
	respawn \
	set-conf-item \
	vdsm \
	write-net-config

dist_man8_MANS = \
	vdsmd.8

vdsm_SUBST = \
	$(nodist_vdsm_DATA) \
	$(nodist_vdsm_SCRIPTS) \
	$(vdsm_data_SUBST) \
	$(dist_man8_MANS)

CLEANFILES = \
	$(vdsm_SUBST) \
	vdsm.conf.sample

EXTRA_DIST = \
	config.py.in \
	libvirt_password \
	logger.conf.in \
	vdsmd.8.in \
	vdsmd.init.in \
	vdsmd.service \
	vdsm-libvirt-access.pkla \
	vdsm-libvirt-logrotate \
	vdsm-logrotate \
	vdsm-logrotate.conf \
	vdsm-restore-net-config.in \
	vdsm.rwtab.in \
	vdsm-sosplugin.py.in \
	vdsm-store-net-config.in

# Reference:
# http://www.gnu.org/software/automake/manual/html_node/Scripts.html
do_subst = sed -e "s,[@]CONFDIR[@],$(vdsmconfdir),g" \
	    -e "s,[@]VDSMLOGDIR[@],$(vdsmlogdir),g" \
	    -e "s,[@]VDSMDIR[@],$(vdsmdir),g" \
	    -e "s,[@]HOOKSDIR[@],$(vdsmhooksdir),g" \
	    -e "s,[@]VDSMRUNDIR[@],$(vdsmrundir),g" \
	    -e "s,[@]VDSMLIBDIR[@],$(vdsmlibdir),g" \
	    -e "s,[@]POOLSDIR[@],$(vdsmpoolsdir),g" \
	    -e "s,[@]BACKUPDIR[@],$(vdsmbackupdir),g" \
	    -e "s,[@]LIBEXECDIR[@],$(vdsmexecdir),g" \
	    -e "s,[@]VDSMREPO[@],$(vdsmrepo),g" \
	    -e "s,[@]TRUSTSTORE[@],$(vdsmtsdir),g"

vdsm.conf.sample: config.py
	python config.py > $@;

$(vdsm_SUBST): Makefile
	if test -f "$@.in"; then \
		$(do_subst) < $@.in > $@; \
	else \
		$(do_subst) < $(srcdir)/$@.in > $@; \
	fi

install-data-hook:
	chmod 440 $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm
	chmod 775 $(DESTDIR)$(localstatedir)/lib/libvirt/qemu/channels

install-data-local: \
		install-data-logger \
		install-data-rwtab \
		install-data-logrotate \
		install-data-sudoers \
		install-data-sosplugin \
		install-data-libvirtpass
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
		uninstall-data-sosplugin \
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

install-data-sosplugin:
	$(MKDIR_P) $(DESTDIR)$(pythondir)/sos/plugins
	$(INSTALL_DATA) vdsm-sosplugin.py \
		$(DESTDIR)$(pythondir)/sos/plugins/vdsm.py

uninstall-data-sosplugin:
	$(RM) $(DESTDIR)$(pythondir)/sos/plugins/vdsm.py
