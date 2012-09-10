#
# Copyright 2011-2012 Red Hat, Inc.
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

dist_vdsmexec_SCRIPTS = \
	vdsm-faqemu

CLEANFILES = \
	config.log \
	$(nodist_vdsmexec_SCRIPTS)

EXTRA_DIST = \
	after_vdsm_stop.py \
	before_vm_start.py

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_vdsm_stop
	$(INSTALL_SCRIPT) $(srcdir)/after_vdsm_stop.py \
		$(DESTDIR)$(vdsmhooksdir)/after_vdsm_stop/10_faqemu
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_vm_start
	$(INSTALL_SCRIPT) $(srcdir)/before_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/before_vm_start/10_faqemu

uninstall-local:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_vdsm_stop/10_faqemu
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_vm_start/10_faqemu
