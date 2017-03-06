#
# Copyright 2008-2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

SUBDIRS = sos storage virt

include $(top_srcdir)/build-aux/Makefile.subs

dist_vdsm_PYTHON = \
	API.py \
	clientIF.py \
	hooking.py \
	sitecustomize.py \
	vdsmd.py \
	$(NULL)

nodist_vdsmexec_SCRIPTS = \
	vdsm-gencerts.sh \
	$(NULL)

nodist_vdsm_SCRIPTS = \
	mk_sysprep_floppy \
	vdsm-store-net-config

dist_vdsm_SCRIPTS = \
	get-conf-item \
	set-conf-item \
	vdsmd \
	vdsm-restore-net-config \
	$(NULL)

CLEANFILES = \
	config.log \
	$(nodist_vdsm_SCRIPTS) \
	$(nodist_vdsmlib_PYTHON) \
	$(nodist_noinst_DATA) \
	$(nodist_vdsmexec_SCRIPTS) \
	$(nodist_man8_MANS)

EXTRA_DIST = \
	dhclientmon.sh \
	libvirt_password \
	mk_sysprep_floppy.in \
	vdsm-gencerts.sh.in \
	vdsm-libvirt-access.pkla \
	vdsm-libvirt-access.rules \
	vdsm-logrotate \
	vdsm-store-net-config.in \
	$(NULL)

all-local: \
	$(nodist_vdsm_SCRIPTS) \
	$(nodist_noinst_DATA) \
	$(nodist_man8_MANS)

install-data-hook:
	chmod 775 $(DESTDIR)$(localstatedir)/lib/libvirt/qemu/channels

install-data-local: \
		install-data-dhclient-hooks \
		install-data-libvirtpass \
		install-data-logrotate \
		install-data-vdsmconf
	$(MKDIR_P) $(DESTDIR)$(vdsmrepo)
	$(MKDIR_P) $(DESTDIR)$(vdsmrepo)/hsm-tasks
	$(MKDIR_P) $(DESTDIR)$(vdsmrepo)/mnt
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/keys
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/certs
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/libvirt-spice
	$(MKDIR_P) $(DESTDIR)$(vdsmlogdir)
	$(MKDIR_P) $(DESTDIR)$(vdsmlogdir)/import
	$(MKDIR_P) $(DESTDIR)$(vdsmrundir)
	$(MKDIR_P) $(DESTDIR)$(vdsmrundir)/dhclientmon
	$(MKDIR_P) $(DESTDIR)$(vdsmrundir)/trackedInterfaces
	$(MKDIR_P) $(DESTDIR)$(vdsmrundir)/payload
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/netconfback
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/persistence
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/staging
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/upgrade
	$(MKDIR_P) $(DESTDIR)$(vdsmbackupdir)
	$(MKDIR_P) $(DESTDIR)$(localstatedir)/lib/libvirt/qemu/channels

uninstall-local: \
		uninstall-data-dhclient-hooks \
		uninstall-data-libvirtpass \
		uninstall-data-logrotate \
		uninstall-data-vdsmconf

install-data-libvirtpass:
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/keys
	$(INSTALL_DATA) -m 600 $(srcdir)/libvirt_password \
		$(DESTDIR)$(vdsmtsdir)/keys/libvirt_password

uninstall-data-libvirtpass:
	$(RM) $(DESTDIR)$(vdsmtsdir)/keys/libvirt_password

install-data-logrotate:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/cron.hourly
	$(INSTALL_SCRIPT) $(srcdir)/vdsm-logrotate \
		$(DESTDIR)$(sysconfdir)/cron.hourly/vdsm-logrotate
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/cron.d

uninstall-data-logrotate:
	$(RM) $(DESTDIR)$(sysconfdir)/cron.hourly/vdsm-logrotate

install-data-vdsmconf:
	$(MKDIR_P) $(DESTDIR)$(vdsmconfdir)/vdsm.conf.d

uninstall-data-vdsmconf:
	$(RM) $(DESTDIR)$(vdsmconfdir)/vdsm.conf.d

install-data-dhclient-hooks:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/dhcp/dhclient.d
	$(INSTALL_DATA) dhclientmon.sh \
		$(DESTDIR)$(sysconfdir)/dhcp/dhclient.d/dhclientmon.sh
	chmod 755 $(DESTDIR)$(sysconfdir)/dhcp/dhclient.d/dhclientmon.sh

uninstall-data-dhclient-hooks:
	$(RM) $(DESTDIR)$(sysconfdir)/dhcp/dhclient.d/dhclientmon.sh
