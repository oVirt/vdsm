#
# Copyright 2008-2018 Red Hat, Inc.
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
TOX_MIN_VERSION = 2.9.1

# We don't want the standard gnu file setup (README, INSTALL, etc.)
AUTOMAKE_OPTIONS = foreign

SUBDIRS = \
	contrib \
	helpers \
	init \
	lib \
	static \
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
	contrib/lvs-stats \
	contrib/profile-stats \
	contrib/repoplot \
	contrib/repostat \
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
	tests \
	$(NULL)

PY3DIVISION_WHITELIST = \
	lib/sos \
	lib/vdsm/api \
	lib/vdsm/common \
	lib/vdsm/gluster \
	lib/vdsm/hook \
	lib/vdsm/host \
	lib/vdsm/metrics \
	lib/vdsm/network \
	lib/vdsm/profiling \
	lib/vdsm/rpc \
	# lib/vdsm/storage \
	lib/vdsm/supervdsm_api \
	lib/vdsm/tool \
	lib/vdsm/virt \
	lib/vdsmclient \
	lib/yajsonrpc \
	tests \
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
	@for f in `git ls-files $(ABS_IMPORT_WHITELIST) |grep '\.py$$\|\.py\.in$$'`; do\
	  if ! grep -q '^from __future__ import absolute_import$$' "$$f"; then \
	    echo "Missing absolute_import in $$f"; exit 1; fi; \
	done;

.PHONY: py3division
py3division: all
	@for f in `git ls-files $(PY3DIVISION_WHITELIST) |grep '\.py$$\|\.py\.in$$'`; do\
	  if ! grep -q '^from __future__ import division$$' "$$f"; then \
	    echo "Missing __future__.division in $$f"; exit 1; fi; \
	done;

.PHONY: pylint
pylint: tox
	tox -e pylint \
		static/usr/share/vdsm/sitecustomize.py \
		lib/vdsm \
		lib/vdsmclient \
		lib/yajsonrpc \
		$(NULL)

pylint-py3k: tox
	tox -e pylint-py3k `build-aux/pylint-py3k-whitelist`

execcmd:
	! git --no-pager grep execCmd `git ls-files | grep -v -F -f execcmd-blacklist.txt`


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
check: gitignore execcmd abs_imports py3division imports python3 flake8 pylint tests

check-recursive:
	@true

.PHONY: check-all
check-all:
	$(MAKE) check NOSE_STRESS_TESTS=1 NOSE_SLOW_TESTS=1

.PHONY: check-unit
check-unit:
	$(MAKE) -C tests/ check NOSE_EVAL_ATTR="type=='unit'"

.PHONY: tests
tests: tests-py27 tests-py36

.PHONY: tests-py27
tests-py27: tox
	tox -e "{tests,storage,lib,network,virt}-py27"

.PHONY: tests-py36
tests-py36: tox
	if [ "$(PYTHON3_SUPPORT)" == "1" ]; then \
		tox -e "{tests,storage,lib,network,virt}-py36"; \
	else \
		echo "Skipping tests for Python 3.6"; \
	fi

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
