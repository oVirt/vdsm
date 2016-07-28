#
# Copyright 2016 Red Hat, Inc.
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

rwtabdir = $(sysconfdir)/rwtab.d

rwtab_DATA = \
	./etc/rwtab.d/vdsm \
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

CLEANFILES = \
	$(rwtab_DATA) \
	$(sudoers_DATA) \
	$(vdsmconf_DATA) \
	$(NULL)

EXTRA_DIST = \
	$(limits_DATA) \
	$(modulesload_DATA) \
	$(sysctl_DATA) \
	$(vdsmconfrotate_DATA) \
	./etc/rwtab.d/vdsm.in \
	./etc/sudoers.d/50_vdsm.in \
	./etc/vdsm/logger.conf.in \
	./etc/vdsm/mom.conf.in \
	./etc/vdsm/svdsm.logger.conf.in \
	$(NULL)
