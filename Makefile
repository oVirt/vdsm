# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#
DIRS=vdsm_cli re vds_bootstrap vdsm vdsm_reg
DOCS=LICENSE_GPL_v2 README 

all: rpm

_lastrelease:=$(shell git describe | cut -d- -f3 | tr -d '[a-zA-Z_]')
_hash:=.g$(shell git rev-parse HEAD | cut -c 1-6)
_branch:=$(shell git branch | grep '^*' | sed 's/^..\(.*\)/.\1/' |\
		 sed 's/[^a-zA-Z0-9_.]//g')
rpmrelease:=$(_lastrelease).1$(_hash)$(_branch)
rpmversion=4.9
RPMTOP=$(shell bash -c "pwd -P")/rpmtop
SPEC=vdsm.spec

TARBALL=vdsm-$(rpmversion)-$(rpmrelease).tar.gz
SRPM=$(RPMTOP)/SRPMS/vdsm-$(rpmversion)-$(rpmrelease).src.rpm

TESTS=pyflakes

test: pyflakes exceptions
	echo $(rpmrelease) $(rpmversion)

exceptions:
	python vdsm/storage/storage_exception.py | grep Collision && exit 1 || true 

pyflakes:
	@which pyflakes > /dev/null && git ls-files '*.py' | xargs pyflakes || (echo "Pyflakes errors or pyflakes not found"; exit 1)

permissive_pyflakes:
	@which pyflakes > /dev/null 2>&1 || (echo "pyflakes not found" && false)
	if git ls-files '*.py' | xargs pyflakes | grep -v used ; then \
	    echo "pyflakes errors" && false ; \
	fi

pylint_all:
	cd vdsm; find storage -name "*.py" | xargs pylint -e 2> /dev/null

pylint:
	cd vdsm; ls storage/*.py | xargs pylint -e 2> /dev/null

.PHONY: tarball
tarball: $(TARBALL)
$(TARBALL): Makefile $(DIRS) $(DOCS) $(TESTS)
	tar zcf $(TARBALL) `git ls-files | grep -v /ut/`	\
			   `git ls-files vdsm/ut/faqemu`

.PHONY: srpm rpm
srpm: $(SRPM)
$(SRPM): $(TARBALL) vdsm.spec.in
	sed 's/^Version:.*/Version: $(rpmversion)/;s/^Release:.*/Release: $(rpmrelease)/' vdsm.spec.in > $(SPEC)
	mkdir -p $(RPMTOP)/{RPMS,SRPMS,SOURCES,BUILD}
	rpmbuild -bs \
	    --define="_topdir $(RPMTOP)" \
	    --define="_sourcedir ." $(SPEC)

rpm: $(SRPM)
	rpmbuild --define="_topdir $(RPMTOP)" --rebuild $<

clean:
	$(RM) *~ *.pyc vdsm*.tar.gz $(SPEC)
	$(RM) -r rpmtop
