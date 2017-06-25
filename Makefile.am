#
# Copyright 2008-2016 Red Hat, Inc.
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
TOX_MIN_VERSION = 2.5.0

# We don't want the standard gnu file setup (README, INSTALL, etc.)
AUTOMAKE_OPTIONS = foreign

SUBDIRS = \
	contrib \
	helpers \
	init \
	lib \
	static \
	vdsm \
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
	README.logging \
	README.md \
	autobuild.sh \
	autogen.sh \
	build-aux/pkg-version \
	build-aux/vercmp \
	contrib/logdb \
	contrib/logstat \
	contrib/profile-stats \
	contrib/repoplot \
	pylintrc \
	vdsm.spec \
	vdsm.spec.in \
	tox.ini \
	$(NULL)

CLEANFILES = \
	vdsm.spec \
	$(DIST_ARCHIVES) \
	$(NULL)

ABS_IMPORT_WHITELIST = \
	lib \
	tests/network/*.py \
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

.PHONY: pylint
pylint: tox
	tox -e pylint \
		vdsm/API.py \
		vdsm/clientIF.py \
		vdsm/sitecustomize.py \
		vdsm/hooking.py \
		vdsm/vdsmd.py \
		vdsm/virt \
		lib/vdsm \
		lib/vdsmclient \
		lib/yajsonrpc \
		$(NULL)

.PHONY: pylint-diff
pylint-diff: tox
	modules=`git diff-tree --no-commit-id --name-only -r HEAD | egrep '\.py$$'`; \
	if [ -n "$$modules" ]; then tox -e pylint $$modules; fi

.PHONY: flake8
flake8: tox
	tox -e flake8

.PHONY: imports
imports: tox
	tox -e imports

.PHONY: tox
tox:
	out=`tox --version`; \
	if [ $$? -ne 0 ]; then \
		echo "Error: cannot run tox, please install tox \
		$(TOX_MIN_VERSION) or later"; \
		exit 1; \
	fi; \
	version=`echo $$out | cut -d' ' -f1`; \
	if build-aux/vercmp $$version $(TOX_MIN_VERSION); then \
		echo "Error: tox is too old, please install tox \
		$(TOX_MIN_VERSION) or later"; \
		exit 1; \
	fi

.PHONY: python3
python3: all
	if [ "$(PYTHON3_SUPPORT)" == "1" ]; then \
	    PYTHONDONTWRITEBYTECODE=1 $(PYTHON3) -m compileall \
		`find -not -path './.tox/*' -name '*.py'` ; \
	else \
	    echo "Warning: skipping python3 syntax check"; \
	fi

# Note: dependencies ordered by time needed to run them
check: gitignore abs_imports imports python3 flake8 pylint tests

check-recursive:
	@true

.PHONY: check-all
check-all:
	$(MAKE) check NOSE_STRESS_TESTS=1 NOSE_SLOW_TESTS=1

.PHONY: check-unit
check-unit:
	$(MAKE) -C tests/ check NOSE_EVAL_ATTR="type=='unit'"

.PHONY: tests
tests:
	tox -e tests,storage-py27,storage-py35,storage-py36

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
