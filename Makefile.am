#
# Copyright 2008-2012 Red Hat, Inc.
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

SUBDIRS = vdsm vdsm_cli vds_bootstrap vdsm_reg vdsm_hooks tests vdsm-tool

include $(top_srcdir)/build-aux/Makefile.subs

# This is an *exception*, we ship also vdsm.spec so it's possible to build the
# rpm from the tarball.
EXTRA_DIST = \
	vdsm.spec \
	vdsm.spec.in

CLEANFILES = \
	$(DIST_ARCHIVES)

if HOOKS
WITH_HOOKS = --define="with_hooks 1"
endif

# When fixing a file to conform with pep8 add it to the WL here so it will be
# checkd from now on
PEP8_WHITELIST = \
	tests \
	vdsm/debugPluginClient.py \
	vdsm/storage/misc.py
	vdsm/storage/processPool.py

check-local:
	$(PYFLAKES) $(SUBDIRS)
	$(PEP8) $(PEP8_WHITELIST)

all-local: \
	vdsm.spec

.PHONY: srpm rpm

srpm: dist
	rpmbuild -ts $(if $(BUILDID),--define="extra_release .$(BUILDID)") $(DIST_ARCHIVES)

rpm: dist
	rpmbuild -ta $(if $(BUILDID),--define="extra_release .$(BUILDID)") \
				$(WITH_HOOKS) $(DIST_ARCHIVES)

dist-hook: gen-ChangeLog

# Generate the ChangeLog file and insert it into the directory
# we're about to use to create a tarball.
.PHONY: gen-ChangeLog
gen-ChangeLog:
	if test -d .git; then					\
	  $(top_srcdir)/build-aux/gitlog-to-changelog		\
	    > $(distdir)/cl-t;					\
	  rm -f $(distdir)/ChangeLog;				\
	  mv $(distdir)/cl-t $(distdir)/ChangeLog;		\
	fi
