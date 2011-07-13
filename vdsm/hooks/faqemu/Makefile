BINDIR=/usr/bin
LIBEXECDIR=/usr/libexec/vdsm
HOOKSDIR=$(LIBEXECDIR)/hooks

install:
	install -d $(DESTDIR)$(BINDIR)
	install -m 755 qemu $(DESTDIR)$(BINDIR)
	ln -sf qemu $(DESTDIR)$(BINDIR)/qemu-system-x86_64
	install -d $(DESTDIR)$(HOOKSDIR)/before_vm_start
	install -m 755 before_vm_start.py $(DESTDIR)$(HOOKSDIR)/before_vm_start/10_faqemu

clean:
	$(RM) *~ *.pyc
