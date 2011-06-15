BINDIR=/usr/bin
LIBEXECDIR=/usr/libexec/vdsm
HOOKSDIR=$(LIBEXECDIR)/hooks

install:
	install -d $(PREFIX)$(BINDIR)
	install -m 755 qemu $(PREFIX)$(BINDIR)
	ln -sf qemu $(PREFIX)$(BINDIR)/qemu-system-x86_64
	install -d $(PREFIX)$(HOOKSDIR)/before_vm_start
	install -m 755 before_vm_start.py $(PREFIX)$(HOOKSDIR)/before_vm_start/10_faqemu

clean:
	$(RM) *~ *.pyc
