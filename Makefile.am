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

if RHEV
SUBDIRS += vdsm_compat
endif

include $(top_srcdir)/build-aux/Makefile.subs

# This is an *exception*, we ship also vdsm.spec so it's possible to build the
# rpm from the tarball.
EXTRA_DIST = \
	.gitignore \
	autobuild.sh \
	autogen.sh \
	build-aux/pkg-version \
	contrib/profile-stats \
	pylintrc \
	vdsm.spec \
	vdsm.spec.in \
	$(NULL)

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

if KOJI
KOJI_BUILD = --define="fedora_koji_build 1"
endif

PEP8_BLACKLIST = \
	config.py \
	constants.py \
	crossImportsTests.py \
	vdsm.py \
	$(NULL)

WHITELIST = \
	contrib/profile-stats \
	init/daemonAdapter \
	vdsm/get-conf-item \
	vdsm/set-conf-item \
	vdsm/supervdsmServer \
	vdsm/vdsm \
	vdsm/vdsm-restore-net-config \
	vdsm/storage/curl-img-wrap \
	vdsm/storage/fc-scan \
	vdsm-tool/vdsm-tool \
	vds_bootstrap/setup \
	vdsm_reg/vdsm-reg-setup.in \
	$(NULL)


.PHONY: gitignore
gitignore:
	@echo "Checking that .in files are ignored..."
	@if test -f .gitignore; then \
	  for i in `git ls-files \*.in`; do \
	    if ! grep -q -x $${i%%.in} .gitignore; then \
	      echo "Missing $${i%%.in} in .gitignore"; exit 1; fi; \
	  done; \
	fi;


SKIP_PYFLAKES_ERR = "\./vdsm/storage/lvm\.py.*: list comprehension redefines \
	'lv' from line .*"

.PHONY: pyflakes
pyflakes:
	python -c 'import pyflakes; print("pyflakes-%s" % pyflakes.__version__)'
	( find . -path './.git' -prune -type f -o \
		-path './vdsm_compat' -prune -type f -o \
		-name '*.py' -o -name '*.py.in' && \
		echo $(WHITELIST) ) | xargs $(PYFLAKES) | \
		grep -w -v $(SKIP_PYFLAKES_ERR) | \
		while read LINE; do echo "$$LINE"; false; done

.PHONY: pep8
pep8:
	$(PEP8) --version
	for x in $(PEP8_BLACKLIST); do \
	    exclude="$${exclude},$${x}" ; \
	done ; \
	$(PEP8) --exclude="$${exclude}" --filename '*.py,*.py.in' . \
	$(WHITELIST)

# Note: dependencies ordered by time needed to run them
check-recursive: gitignore pyflakes pep8

.PHONY: check-all
check-all:
	$(MAKE) check NOSE_STRESS_TESTS=1 NOSE_SLOW_TESTS=1

all-local: \
	vdsm.spec

.PHONY: srpm rpm

srpm: dist
	rpmbuild -ts $(if $(BUILDID),--define="extra_release .$(BUILDID)") $(DIST_ARCHIVES)

rpm: dist
	rpmbuild -ta $(if $(BUILDID),--define="extra_release .$(BUILDID)") \
			$(WITH_HOOKS) $(RHEV_BUILD) $(KOJI_BUILD) $(DIST_ARCHIVES)

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
