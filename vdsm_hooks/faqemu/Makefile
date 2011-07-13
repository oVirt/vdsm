INSTALL=install
LN_S=ln -s
BINDIR=/usr/bin
LIBEXECDIR=/usr/libexec/vdsm
HOOKSDIR=$(LIBEXECDIR)/hooks

install:
	$(INSTALL) -Dm 755 qemu $(DESTDIR)$(BINDIR)/qemu
	$(LN_S) qemu $(DESTDIR)$(BINDIR)/qemu-system-x86_64
	$(INSTALL) -Dm 755 before_vm_start.py $(DESTDIR)$(HOOKSDIR)/before_vm_start/10_faqemu

clean:
	$(RM) *~ *.pyc
