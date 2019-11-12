# Copyright 2017 Red Hat, Inc.
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

vdsmapidir = $(vdsmpylibdir)/api
vdsmrpcdir = $(vdsmpylibdir)/rpc

dist_vdsmapi_PYTHON = \
	__init__.py \
	schema_inconsistency_formatter.py \
	vdsmapi.py \
	$(NULL)

CLEANFILES = \
	vdsm-api.html \
	vdsm-api.pickle \
	vdsm-api-gluster.pickle \
	vdsm-events.pickle \
	$(NULL)

EXTRA_DIST = \
	schema_to_html.py \
	$(NULL)

dist_vdsmrpc_DATA = \
	vdsm-api.pickle \
	vdsm-api-gluster.pickle \
	vdsm-events.pickle \
	$(NULL)

nodist_noinst_DATA = \
	vdsm-api.html \
	$(NULL)

vdsm-api.html: schema_to_html.py vdsm-api.pickle
	@echo "  Generate $@"
	chmod u+w $(srcdir)
	PYTHONPATH=$(srcdir)/../../:$(srcdir)/../../vdsm \
		$(srcdir)/schema_to_html.py vdsm-api $(srcdir)/$@

%.pickle: %.yml schema_to_pickle.py
	@echo "  Generate $@"
	chmod u+w $(srcdir)
	PYTHONPATH=$(srcdir)/../../:$(srcdir)/../../vdsm \
		$(srcdir)/schema_to_pickle.py $< $@

install-data-local: install-dist_vdsmrpcDATA
	chmod 444 "$(DESTDIR)$(vdsmrpcdir)"/*.pickle
