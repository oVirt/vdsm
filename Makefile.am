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

SUBDIRS = \
	client \
	contrib \
	debian \
	init \
	lib \
	vds_bootstrap \
	vdsm \
	vdsm-tool \
	vdsm_hooks \
	vdsm_reg \
	$(NULL)

# The tests should be always last as they need the rest of the source to be
# prepared before running.
SUBDIRS += tests

include $(top_srcdir)/build-aux/Makefile.subs

# This is an *exception*, we ship also vdsm.spec so it's possible to build the
# rpm from the tarball.
EXTRA_DIST = \
	build-aux/pkg-version \
	contrib/profile-stats \
	vdsm.spec \
	vdsm.spec.in

CLEANFILES = \
	vdsm.spec \
	$(DIST_ARCHIVES) \
	$(NULL)

if HOOKS
WITH_HOOKS = --define="with_hooks 1"
endif

if RHEV
RHEV_BUILD = --define="rhev_build 1"
endif

PEP8_BLACKLIST = \
	config.py \
	constants.py \
	crossImportsTests.py \
	vdsm.py \
	$(NULL)

PEP8_WHITELIST = \
	contrib/profile-stats \
	init/daemonAdapter \
	vdsm/get-conf-item \
	vdsm/set-conf-item \
	vdsm/supervdsmServer \
	vdsm/vdsm \
	vdsm/vdsm-restore-net-config \
	vdsm/storage/curl-img-wrap \
	vdsm-tool/vdsm-tool \
	vds_bootstrap/setup \
	$(NULL)


SKIP_PYFLAKES_ERR = "\./vdsm/storage/lvm\.py.*: list comprehension redefines \
	'lv' from line .*"

check-local:
	python -c 'import pyflakes; print("pyflakes-%s" % pyflakes.__version__)'
	find . -path './.git' -prune -type f -o \
		-name '*.py' -o -name '*.py.in'  | xargs $(PYFLAKES) | \
		grep -w -v $(SKIP_PYFLAKES_ERR) | \
		while read LINE; do echo "$$LINE"; false; done
	$(PEP8) --version
	for x in $(PEP8_BLACKLIST); do \
	    exclude="$${exclude},$${x}" ; \
	done ; \
	$(PEP8) --exclude="$${exclude}" --filename '*.py,*.py.in' .
	$(PEP8) $(PEP8_WHITELIST)
	@if test -f .gitignore; then \
	  for i in `git ls-files \*.in`; do \
	    if ! grep -q -x $${i%%.in} .gitignore; then \
	      echo "Missing $${i%%.in} in .gitignore"; exit 1; fi; \
	  done; \
	fi;

all-local: \
	vdsm.spec

.PHONY: srpm rpm

srpm: dist
	rpmbuild -ts $(if $(BUILDID),--define="extra_release .$(BUILDID)") $(DIST_ARCHIVES)

rpm: dist
	rpmbuild -ta $(if $(BUILDID),--define="extra_release .$(BUILDID)") \
				$(WITH_HOOKS) $(RHEV_BUILD) $(DIST_ARCHIVES)

dist-hook: gen-VERSION gen-ChangeLog
.PHONY: gen-VERSION gen-ChangeLog

# Generate the ChangeLog file and insert it into the directory
# we're about to use to create a tarball.
gen-ChangeLog:
	if test -d .git; then					\
	  $(top_srcdir)/build-aux/gitlog-to-changelog		\
	    > $(distdir)/cl-t;					\
	  rm -f $(distdir)/ChangeLog;				\
	  mv $(distdir)/cl-t $(distdir)/ChangeLog;		\
	fi

gen-VERSION:
	if test -d .git; then					\
	  $(top_srcdir)/build-aux/pkg-version --full		\
	    > $(distdir)/ve-t;					\
	  rm -f $(distdir)/VERSION;				\
	  mv $(distdir)/ve-t $(distdir)/VERSION;		\
	fi
