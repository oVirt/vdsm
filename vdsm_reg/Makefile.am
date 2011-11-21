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
	define.py

nodist_vdsmreg_DATA = \
	deployUtil.py

nodist_vdsmbootstrap_DATA = \
        deployUtil.py

nodist_noinst_DATA = \
	vdsm-reg.conf

dist_vdsmreg_SCRIPTS = \
	save-config \
	vdsm-complete \
	vdsm-gen-cert \
	vdsm-reg-setup \
	vdsm-upgrade

dist_man8_MANS = \
	vdsm-reg.8

vdsmreg_SUBST = \
	$(nodist_noinst_DATA)

CLEANFILES = \
	$(vdsmreg_SUBST)

EXTRA_DIST = \
	deployUtil.py.in \
	logger.conf \
	rhevm.py \
	vdsm-config \
	vdsm-reg-cmdline \
	vdsm-reg.conf.in \
	vdsm-reg.init \
	vdsm-reg-logrotate \
	vdsm-reg-logrotate.conf \
	vdsm-reg.service

# Reference:
# http://www.gnu.org/software/automake/manual/html_node/Scripts.html
do_subst = sed -e "s,[@]VDSMRUNDIR[@],$(vdsmrundir),g"

$(vdsmreg_SUBST):
	$(do_subst) < $(srcdir)/$@.in > $@

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
	$(MKDIR_P) $(DESTDIR)$(pythondir)/ovirt_config_setup
	$(INSTALL_DATA) $(srcdir)/rhevm.py \
		$(DESTDIR)$(pythondir)/ovirt_config_setup/rhevm.py

uninstall-data-ovirt:
	$(RM) $(DESTDIR)$(sysconfdir)/ovirt-config-boot.d/vdsm-config
	$(RM) $(DESTDIR)$(pythondir)/ovirt_config_setup/rhevm.py

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
