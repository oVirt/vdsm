#
# Copyright 2016-2017 Red Hat, Inc.
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

include $(top_srcdir)/build-aux/Makefile.subs

man8_MANS = \
	./usr/share/man/man8/vdsmd.8

dist_man1_MANS = \
	./usr/share/man/man1/vdsm-client.1 \
	./usr/share/man/man1/vdsm-tool.1

dist_bin_SCRIPTS = \
	./usr/bin/vdsm-client \
	./usr/bin/vdsm-tool

vdsm_DATA = \
	./usr/share/vdsm/bonding-defaults.json \
	./usr/share/vdsm/bonding-name2numeric.json \
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

dist_vdsm_SCRIPTS = \
	./usr/sbin/supervdsmd \
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

networkmanagerdir = $(sysconfdir)/NetworkManager/conf.d

networkmanager_DATA = \
	./etc/NetworkManager/conf.d/vdsm.conf \
	$(NULL)

systemdunitdir = $(SYSTEMD_UNIT_DIR)

systemdunit_DATA = \
	./usr/lib/systemd/system/mom-vdsm.service \
	./usr/lib/systemd/system/supervdsmd.service \
	./usr/lib/systemd/system/vdsm-network.service \
	./usr/lib/systemd/system/vdsm-network-init.service \
	./usr/lib/systemd/system/vdsmd.service \
	$(NULL)

dist_systemdunit_DATA = \
	./usr/lib/systemd/system/dev-hugepages1G.mount \
	$(NULL)

CLEANFILES = \
	$(sudoers_DATA) \
	$(systemdunit_DATA) \
	$(vdsmconf_DATA) \
	$(man8_MANS) \
	$(NULL)

EXTRA_DIST = \
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
	./usr/lib/systemd/system/mom-vdsm.service.in \
	./usr/lib/systemd/system/supervdsmd.service.in \
	./usr/lib/systemd/system/vdsm-network.service.in \
	./usr/lib/systemd/system/vdsm-network-init.service.in \
	./usr/lib/systemd/system/vdsmd.service.in \
	./usr/share/man/man1/vdsm-tool.1.in \
	./usr/share/man/man8/vdsmd.8.in \
	$(NULL)

usr/share/vdsm/autounattend/%.xml: usr/share/vdsm/autounattend/Autounattend.xml.in
	sed -e 's/@DRIVER_DIR@/$*/' < $< > $@
