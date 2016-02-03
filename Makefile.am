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
	vdsm \
	vdsm-tool \
	vdsm_hooks \
	$(NULL)

# The tests should be always last as they need the rest of the source to be
# prepared before running.
SUBDIRS += tests

include $(top_srcdir)/build-aux/Makefile.subs

# This is an *exception*, we ship also vdsm.spec so it's possible to build the
# rpm from the tarball.
EXTRA_DIST = \
	.gitignore \
	autobuild.sh \
	autogen.sh \
	build-aux/pkg-version \
	contrib/logdb \
	contrib/profile-stats \
	pylintrc \
	vdsm.spec \
	vdsm.spec.in \
	$(NULL)

CLEANFILES = \
	vdsm.spec \
	$(DIST_ARCHIVES) \
	$(NULL)

PEP8_BLACKLIST = \
	config.py \
	constants.py \
	crossImportsTests.py \
	vdsm.py \
	$(NULL)

ABS_IMPORT_WHITELIST = \
	lib \
	$(NULL)

WHITELIST = \
	contrib/logdb \
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

.PHONY: abs_imports
abs_imports: all
	@for f in `git ls-files $(ABS_IMPORT_WHITELIST) |grep '.py$$'`; do\
	  if ! grep -q '^from __future__ import absolute_import$$' "$$f"; then \
	    echo "Missing absolute_import in $$f"; exit 1; fi; \
	done;

.PHONY: pyflakes
pyflakes: all
	python -c 'import pyflakes; print("pyflakes-%s" % pyflakes.__version__)'
	( find . -path './.git' -prune -type f -o \
		-name '*.py' && \
		echo $(WHITELIST) ) | xargs $(PYFLAKES)

.PHONY: pep8
pep8: all
	$(PEP8) --version
	for x in $(PEP8_BLACKLIST); do \
	    exclude="$${exclude},$${x}" ; \
	done ; \
	$(PEP8) --exclude="$${exclude}" --filename '*.py' . \
	$(WHITELIST)

.PHONY: python3
python3: all
	if [ -x "$(PYTHON3)" ]; then \
	    PYTHONDONTWRITEBYTECODE=1 $(PYTHON3) -m compileall \
		$(WHITELIST) \
		`find . -name '*.py'`; \
	else \
	    echo "Warning: skipping python3 syntax check"; \
	fi

# Note: dependencies ordered by time needed to run them
check-recursive: gitignore abs_imports python3 pyflakes pep8

.PHONY: check-all
check-all:
	$(MAKE) check NOSE_STRESS_TESTS=1 NOSE_SLOW_TESTS=1

all-local: \
	vdsm.spec

.PHONY: srpm rpm

srpm: dist
	rpmbuild -ts $(if $(BUILDID),--define="extra_release .$(BUILDID)") \
		$(DIST_ARCHIVES)

rpm: dist
	rpmbuild -ta $(if $(BUILDID),--define="extra_release .$(BUILDID)") \
		$(DIST_ARCHIVES)

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
