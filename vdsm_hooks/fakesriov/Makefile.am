#
# Copyright 2015 Red Hat, Inc.
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

CLEANFILES = \
	config.log

EXTRA_DIST = \
	after_hostdev_list_by_caps.py \
	after_get_caps.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_hostdev_list_by_caps
	$(INSTALL_SCRIPT) $(srcdir)/after_hostdev_list_by_caps.py \
		$(DESTDIR)$(vdsmhooksdir)/after_hostdev_list_by_caps/10_fakesriov
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_caps
	$(INSTALL_SCRIPT) $(srcdir)/after_get_caps.py \
		$(DESTDIR)$(vdsmhooksdir)/after_get_caps/10_fakesriov

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_hostdev_list_by_caps/10_fakesriov
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_caps/10_fakesriov
