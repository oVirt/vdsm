#
# Copyright 2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
#

EXTRA_DIST = \
	12-vdsm-localdisk.rules \
	after_disk_prepare \
	localdisk-helper \
	sudoers.vdsm_hook_localdisk \
	before_vm_migrate_source \
	$(NULL)

UDEV_RULES_DIR = /usr/lib/udev/rules.d

install-data-local: install-data-sudoers
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_disk_prepare
	$(INSTALL_SCRIPT) $(srcdir)/after_disk_prepare \
		$(DESTDIR)$(vdsmhooksdir)/after_disk_prepare/localdisk
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_migrate_source \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source/localdisk
	$(INSTALL_SCRIPT) $(srcdir)/localdisk-helper \
		$(DESTDIR)$(vdsmexecdir)/localdisk-helper
	$(MKDIR_P) $(DESTDIR)$(UDEV_RULES_DIR)
	$(INSTALL_DATA) $(srcdir)/12-vdsm-localdisk.rules \
		$(DESTDIR)$(UDEV_RULES_DIR)/12-vdsm-localdisk.rules

uninstall-local: uninstall-data-sudoers
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_disk_prepare/localdisk
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_migrate_source/localdisk
	$(RM) $(DESTDIR)$(vdsmexecdir)/localdisk-helper
	$(RM) $(DESTDIR)$(UDEV_RULES_DIR)/12-vdsm-localdisk.rules

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) -m 440 $(srcdir)/sudoers.vdsm_hook_localdisk \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_localdisk

uninstall-data-sudoers:
	$(RM) $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_localdisk

