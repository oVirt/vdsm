# Copyright 2006 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

nodist_bin_SCRIPTS = \
	vdsClient

dist_vdsm_DATA = \
	dumpStorageTable.py \
	vdsClient.py \
	vdscli.py

dist_man1_MANS = \
	vdsClient.1

vdsmcli_SUBST = \
	vdsClient \
	vdscli.py

CLEANFILES = \
	$(vdsmcli_SUBST)

EXTRA_DIST = \
	vdsClient.completion \
	vdsClient.in \
	vdscli.py.in

# Reference:
# http://www.gnu.org/software/automake/manual/html_node/Scripts.html
do_subst = sed -e "s,[@]VDSMDIR[@],$(vdsmdir),g" \
	    -e "s,[@]TRUSTSTORE[@],$(vdsmtsdir),g"

$(vdsmcli_SUBST):
	$(do_subst) < $(srcdir)/$@.in > $@

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/bash_completion.d
	$(INSTALL_DATA) $(srcdir)/vdsClient.completion \
		$(DESTDIR)$(sysconfdir)/bash_completion.d/vdsClient

uninstall-local:
	$(RM) $(DESTDIR)$(sysconfdir)/bash_completion.d/vdsClient
