# Copyright 2006 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#
TARGET       = /usr/share/vdsm
PYSRCS       = vdsClient.py vdscli.py dumpStorageTable.py
CONFDIR      = /etc/vdsm
BINDIR       = bin
VDSMDIR      = /usr/share/vdsm
COMPDIR      = /etc/bash_completion.d
TRUSTSTORE   = /etc/pki/vdsm
MANDIR	     = /usr/share/man

all: rpm

fixpaths:
	sed -i "s:@VDSMDIR@:$(VDSMDIR):" vdsClient
	sed -i "s:@TRUSTSTORE@:$(TRUSTSTORE):" vdscli.py

install: fixpaths
	mkdir -p ${PREFIX}/$(TARGET)
	cp ${PYSRCS} ${PREFIX}/$(TARGET)
	mkdir -p $(PREFIX)/$(BINDIR)
	cp vdsClient $(PREFIX)/$(BINDIR)
	mkdir -p $(PREFIX)/$(COMPDIR)
	cp vdsClient.completion $(PREFIX)$(COMPDIR)/vdsClient
	mkdir -p $(PREFIX)$(MANDIR)/man1
	cp vdsClient.1 $(PREFIX)$(MANDIR)/man1/

clean:
	$(RM) *~ *.pyc
