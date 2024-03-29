# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

include $(top_srcdir)/build-aux/Makefile.subs

man8_MANS = \
	./usr/share/man/man8/vdsmd.8

dist_man1_MANS = \
	./usr/share/man/man1/vdsm-client.1 \
	./usr/share/man/man1/vdsm-tool.1

dist_bin_SCRIPTS = \
	./usr/bin/vdsm-client \
	./usr/bin/vdsm-tool

dist_vdsmexec_PYTHON = \
	./libexec/vdsm/sitecustomize.py \
	$(NULL)

vdsm_DATA = \
	./usr/share/vdsm/lvmlocal.conf \
	$(NULL)

vdsm_autounattenddir = $(vdsmdir)/autounattend

vdsm_autounattend_DATA = \
	./usr/share/vdsm/autounattend/Win7.xml \
	./usr/share/vdsm/autounattend/Win8.xml \
	./usr/share/vdsm/autounattend/Win8.1.xml \
	./usr/share/vdsm/autounattend/Win10.xml \
	./usr/share/vdsm/autounattend/Win2008.xml \
	./usr/share/vdsm/autounattend/Win2008R2.xml \
	./usr/share/vdsm/autounattend/Win2012.xml \
	./usr/share/vdsm/autounattend/Win2012R2.xml \
	./usr/share/vdsm/autounattend/Win2016.xml \
	$(NULL)

vdsmconf_DATA = \
	./etc/vdsm/logger.conf \
	./etc/vdsm/mom.conf \
	./etc/vdsm/svdsm.logger.conf \
	$(NULL)

vdsmconfmomdir = $(vdsmconfdir)/mom.d

dist_vdsmconfmom_DATA = \
	./etc/vdsm/mom.d/00-defines.policy \
	./etc/vdsm/mom.d/01-parameters.policy \
	./etc/vdsm/mom.d/02-balloon.policy \
	./etc/vdsm/mom.d/03-ksm.policy \
	./etc/vdsm/mom.d/04-cputune.policy \
	./etc/vdsm/mom.d/05-iotune.policy \
	$(NULL)

vdsmconfrotatedir = $(vdsmconfdir)/logrotate

vdsmconfrotate_DATA = \
	./etc/vdsm/logrotate/vdsm \
	$(NULL)

sudoersdir = $(sysconfdir)/sudoers.d

sudoers_DATA = \
	./etc/sudoers.d/50_vdsm \
	$(NULL)

limitsdir = $(sysconfdir)/security/limits.d

limits_DATA = \
	./etc/security/limits.d/99-vdsm.conf \
	$(NULL)

modulesloaddir = $(sysconfdir)/modules-load.d

modulesload_DATA = \
	./etc/modules-load.d/vdsm.conf \
	$(NULL)

sysctldir = $(sysconfdir)/sysctl.d

sysctl_DATA = \
	./etc/sysctl.d/vdsm.conf \
	$(NULL)

modprobedir = $(sysconfdir)/modprobe.d

modprobe_DATA = \
	./etc/modprobe.d/vdsm-bonding-modprobe.conf \
	$(NULL)

libvirtpassworddir = $(vdsmtsdir)/keys
libvirtpassword_DATA = \
	./etc/pki/vdsm/keys/libvirt_password \
	$(NULL)

dist_polkit_DATA = \
	./usr/share/polkit-1/rules.d/10-vdsm-libvirt-access.rules \
	$(NULL)

cronhourlydir = $(sysconfdir)/cron.hourly
dist_cronhourly_SCRIPTS = \
	./etc/cron.hourly/vdsm-logrotate \
	$(NULL)

dist_vdsmexec_SCRIPTS = \
	./libexec/vdsm/get-conf-item \
	./libexec/vdsm/supervdsmd \
	./libexec/vdsm/vdsm-gencerts.sh \
	./libexec/vdsm/vdsmd \
	$(NULL)

networkmanagerdir = $(sysconfdir)/NetworkManager/conf.d

networkmanager_DATA = \
	./etc/NetworkManager/conf.d/vdsm.conf \
	$(NULL)

libvirthookdir = $(sysconfdir)/libvirt/hooks
dist_libvirthook_SCRIPTS = \
	./etc/libvirt/hooks/qemu \
        $(NULL)

systemdunitdir = $(SYSTEMD_UNIT_DIR)

systemdunit_DATA = \
	./usr/lib/systemd/system/mom-vdsm.service \
	./usr/lib/systemd/system/supervdsmd.service \
	./usr/lib/systemd/system/vdsm-network.service \
	./usr/lib/systemd/system/vdsmd.service \
	$(NULL)

dist_systemdunit_DATA = \
	./usr/lib/systemd/system/dev-hugepages1G.mount \
	$(NULL)

imageiodir = $(sysconfdir)/ovirt-imageio/conf.d

imageio_DATA = \
	./etc/ovirt-imageio/conf.d/60-vdsm.conf \
	$(NULL)

CLEANFILES = \
	$(sudoers_DATA) \
	$(systemdunit_DATA) \
	$(vdsmconf_DATA) \
	$(man8_MANS) \
	$(nodist_vdsmexec_SCRIPTS) \
	$(NULL)

EXTRA_DIST = \
	$(imageio_DATA) \
	$(libvirtpassword_DATA) \
	$(limits_DATA) \
	$(modprobe_DATA) \
	$(modulesload_DATA) \
	$(networkmanager_DATA) \
	$(sysctl_DATA) \
	$(vdsmconfrotate_DATA) \
	$(vdsm_DATA) \
	$(vdsm_autounattend_DATA) \
	./etc/sudoers.d/50_vdsm.in \
	./etc/vdsm/logger.conf.in \
	./etc/vdsm/mom.conf.in \
	./etc/vdsm/svdsm.logger.conf.in \
	./libexec/vdsm/vdsm-gencerts.sh.in \
	./usr/lib/systemd/system/mom-vdsm.service.in \
	./usr/lib/systemd/system/supervdsmd.service.in \
	./usr/lib/systemd/system/vdsm-network.service.in \
	./usr/lib/systemd/system/vdsmd.service.in \
	./usr/share/man/man1/vdsm-tool.1.in \
	./usr/share/man/man8/vdsmd.8.in \
	$(NULL)

usr/share/vdsm/autounattend/%.xml: usr/share/vdsm/autounattend/Autounattend.xml.in
	sed -e 's/@DRIVER_DIR@/$*/' < $< > $@

install-data-local: install-libvirtpasswordDATA
	$(MKDIR_P) $(DESTDIR)$(vdsmconfdir)/vdsm.conf.d
	$(MKDIR_P) $(DESTDIR)$(vdsmrepo)
	$(MKDIR_P) $(DESTDIR)$(vdsmrepo)/hsm-tasks
	$(MKDIR_P) $(DESTDIR)$(vdsmrepo)/mnt
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/keys
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/certs
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/libvirt-migrate
	$(MKDIR_P) $(DESTDIR)$(vdsmtsdir)/libvirt-spice
	$(MKDIR_P) $(DESTDIR)$(vdsmlogdir)
	$(MKDIR_P) --mode=0750 $(DESTDIR)$(vdsmlogdir)/commands
	$(MKDIR_P) $(DESTDIR)$(vdsmlogdir)/import
	$(MKDIR_P) $(DESTDIR)$(vdsmrundir)
	$(MKDIR_P) $(DESTDIR)$(vdsmrundir)/payload
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/netconfback
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/persistence
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/staging
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/storage
	$(MKDIR_P) $(DESTDIR)$(vdsmlibdir)/upgrade
	$(MKDIR_P) $(DESTDIR)$(vdsmbackupdir)
	$(MKDIR_P) $(DESTDIR)$(localstatedir)/lib/libvirt/qemu/channels
	chmod 600 "$(DESTDIR)$(libvirtpassworddir)"/*
	chmod 775 $(DESTDIR)$(localstatedir)/lib/libvirt/qemu/channels
	install -dDm 0755 $(DESTDIR)$(vdsmlogdir)
	touch $(DESTDIR)$(vdsmlogdir)/mom.log
	touch $(DESTDIR)$(vdsmlogdir)/supervdsm.log
	touch $(DESTDIR)$(vdsmlogdir)/vdsm.log
