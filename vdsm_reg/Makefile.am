#
# Copyright 2008-2011 Red Hat, Inc.
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

include $(top_srcdir)/build-aux/Makefile.subs

ovirtsetupdir = $(pythondir)/ovirt_config_setup

dist_vdsmreg_PYTHON = \
	config.py \
	createDaemon.py \
	define.py \
	register-to-engine.py

dist_ovirtsetup_PYTHON = \
	engine.py

nodist_vdsmreg_PYTHON = \
	deployUtil.py

nodist_vdsmbootstrap_DATA = \
        deployUtil.py

nodist_noinst_DATA = \
	vdsm-reg.conf

dist_vdsmreg_SCRIPTS = \
	save-config \
	vdsm-complete \
	vdsm-gen-cert \
	vdsm-upgrade

nodist_vdsmreg_SCRIPTS = \
	vdsm-reg-setup

dist_man8_MANS = \
	vdsm-reg.8

CLEANFILES = \
	config.log \
	$(nodist_noinst_DATA) \
	$(dist_ovirtsetup_PYTHON) \
	$(nodist_vdsmreg_SCRIPTS) \
	$(nodist_vdsmreg_PYTHON) \
	$(nodist_vdsmbootstrap_PYTHON)

EXTRA_DIST = \
	deployUtil.py.in \
	logger.conf \
	vdsm-config \
	vdsm-reg-cmdline \
	vdsm-reg.conf.in \
	vdsm-reg.init \
	vdsm-reg-logrotate \
	vdsm-reg-logrotate.conf \
	vdsm-reg.service \
	vdsm-reg-setup.in

all-local: \
	$(nodist_noinst_DATA) \
	$(dist_ovirtsetup_PYTHON) \
	$(nodist_vdsmreg_SCRIPTS)

install-data-local: \
		install-data-logger \
		install-data-ovirt \
		install-data-logrotate
	$(MKDIR_P)  $(DESTDIR)$(vdsmreglogdir)

uninstall-local: \
		uninstall-data-logger \
		uninstall-data-ovirt \
		uninstall-data-logrotate

install-data-logger:
	$(MKDIR_P) $(DESTDIR)$(vdsmregconfdir)
	$(INSTALL_DATA) $(srcdir)/logger.conf \
		$(DESTDIR)$(vdsmregconfdir)/logger.conf
	$(INSTALL_DATA) vdsm-reg.conf \
		$(DESTDIR)$(vdsmregconfdir)/vdsm-reg.conf

uninstall-data-logger:
	$(RM) $(DESTDIR)$(vdsmregconfdir)/logger.conf
	$(RM) $(DESTDIR)$(vdsmregconfdir)/vdsm-reg.conf

install-data-ovirt:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/ovirt-config-boot.d
	$(INSTALL_SCRIPT) $(srcdir)/vdsm-config \
		$(DESTDIR)$(sysconfdir)/ovirt-config-boot.d/vdsm-config
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/ovirt-commandline.d
	$(INSTALL_DATA) $(srcdir)/vdsm-reg-cmdline \
		$(DESTDIR)$(sysconfdir)/ovirt-commandline.d/vdsm-reg

uninstall-data-ovirt:
	$(RM) $(DESTDIR)$(sysconfdir)/ovirt-config-boot.d/vdsm-config

install-data-logrotate:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/logrotate.d
	$(INSTALL_DATA) $(srcdir)/vdsm-reg-logrotate.conf \
		$(DESTDIR)$(sysconfdir)/logrotate.d/vdsm-reg
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/cron.hourly
	$(INSTALL_SCRIPT) $(srcdir)/vdsm-reg-logrotate \
		$(DESTDIR)$(sysconfdir)/cron.hourly/vdsm-reg-logrotate

uninstall-data-logrotate:
	$(RM) $(DESTDIR)$(sysconfdir)/logrotate.d/vdsm-reg
	$(RM) $(DESTDIR)$(sysconfdir)/cron.hourly/vdsm-reg-logrotate
