# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

TARGET       = /usr/share/vdsm-bootstrap
PYSRCS       = vds_bootstrap.py deployUtil.py vds_bootstrap_complete.py

all: $(PYSRCS)

install:
	mkdir -p $(DESTDIR)/$(TARGET)
	cp $(PYSRCS) $(DESTDIR)/$(TARGET)

clean:
	$(RM) *.pyc *.pyo  *~

pyflakes:
	find . -name "*.py" | xargs pyflakes

