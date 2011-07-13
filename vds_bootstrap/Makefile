# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

INSTALL      = install
VDSMBOOTDIR  = /usr/share/vdsm-bootstrap
PYSRCS       = vds_bootstrap.py vds_bootstrap_complete.py

all: $(PYSRCS)

install:
	$(INSTALL) -Dd 755 $(DESTDIR)$(VDSMBOOTDIR)
	$(INSTALL) -Dm 755 $(PYSRCS) $(DESTDIR)$(VDSMBOOTDIR)
	$(INSTALL) -Dm 644 deployUtil.py $(DESTDIR)$(VDSMBOOTDIR)

clean:
	$(RM) *.pyc *.pyo  *~

pyflakes:
	find . -name "*.py" | xargs pyflakes

