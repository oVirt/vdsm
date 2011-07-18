# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

dist_vdsmreg_DATA = \
	config.py \
	createDaemon.py \
	define.py \
	deployUtil.py

dist_vdsmreg_SCRIPTS = \
	save-config \
	vdsm-complete \
	vdsm-gen-cert \
	vdsm-reg-setup \
	vdsm-upgrade

dist_man8_MANS = \
	vdsm-reg.8

noinst_DATA = \
	vdsm-reg.conf

vdsmreg_SUBST = \
	$(noinst_DATA)

CLEANFILES = \
	$(vdsmreg_SUBST)

EXTRA_DIST = \
	logger.conf \
	rhevm.py \
	vdsm-config \
	vdsm-reg \
	vdsm-reg.conf.in \
	vdsm-reg-logrotate \
	vdsm-reg-logrotate.conf

# Reference:
# http://www.gnu.org/software/automake/manual/html_node/Scripts.html
do_subst = sed -e "s,[@]VDSMRUNDIR[@],$(vdsmrundir),g"

$(vdsmreg_SUBST):
	$(do_subst) < $@.in > $@

install-data-local: install-data-init install-data-logger install-data-ovirt \
		install-data-logrotate
	$(MKDIR_P)  $(DESTDIR)$(vdsmreglogdir)

install-data-init: $(vdsmreg_SUBST)
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/init.d
	$(INSTALL_SCRIPT) vdsm-reg $(DESTDIR)$(sysconfdir)/init.d/vdsm-reg

install-data-logger: $(vdsmreg_SUBST)
	$(MKDIR_P) $(DESTDIR)$(vdsmregconfdir)
	$(INSTALL_DATA) logger.conf $(DESTDIR)$(vdsmregconfdir)/logger.conf
	$(INSTALL_DATA) vdsm-reg.conf $(DESTDIR)$(vdsmregconfdir)/vdsm-reg.conf

install-data-ovirt: $(vdsmreg_SUBST)
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/ovirt-config-boot.d
	$(INSTALL_SCRIPT) vdsm-config $(DESTDIR)$(sysconfdir)/ovirt-config-boot.d/vdsm-config
	$(MKDIR_P) $(DESTDIR)$(pythondir)/ovirt_config_setup
	$(INSTALL_DATA) rhevm.py $(DESTDIR)$(pythondir)/ovirt_config_setup/rhevm.py

install-data-logrotate: $(vdsmreg_SUBST)
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/logrotate.d
	$(INSTALL_DATA) vdsm-reg-logrotate.conf \
		$(DESTDIR)$(sysconfdir)/logrotate.d/vdsm-reg
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/cron.hourly
	$(INSTALL_SCRIPT) vdsm-reg-logrotate \
		$(DESTDIR)$(sysconfdir)/cron.hourly/vdsm-reg-logrotate
