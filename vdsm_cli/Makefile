# Copyright 2006 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

INSTALL      = install

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
	$(INSTALL) -Dd $(DESTDIR)/$(TARGET)
	$(INSTALL) -Dm 644 $(PYSRCS) $(DESTDIR)/$(TARGET)
	$(INSTALL) -Dm 755 vdsClient $(DESTDIR)/$(BINDIR)/vdsClient
	$(INSTALL) -Dm 644 vdsClient.completion $(DESTDIR)$(COMPDIR)/vdsClient
	$(INSTALL) -Dm 644 vdsClient.1 $(DESTDIR)$(MANDIR)/man1/vdsClient.1

clean:
	$(RM) *~ *.pyc
