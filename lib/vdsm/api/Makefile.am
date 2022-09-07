# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
