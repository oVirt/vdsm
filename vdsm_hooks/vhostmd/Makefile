INSTALL=install
LN_S=ln -s
LIBEXECDIR=/usr/libexec/vdsm
HOOKSDIR=$(LIBEXECDIR)/hooks

all:

install:
	$(INSTALL) -Dm 755 before_vm_start.py $(DESTDIR)$(HOOKSDIR)/before_vm_start/50_vhostmd
	$(LN_S) ../before_vm_start/50_vhostmd $(DESTDIR)$(HOOKSDIR)/before_vm_migrate_destination/50_vhostmd
	$(LN_S) ../before_vm_start/50_vhostmd $(DESTDIR)$(HOOKSDIR)/before_vm_dehibernate/50_vhostmd
	$(INSTALL) -Dm 755 after_vm_destroy.py $(DESTDIR)$(HOOKSDIR)/after_vm_destroy/50_vhostmd
	$(INSTALL) -Dm 440 sudoers.vdsm_hook_vhostmd $(DESTDIR)/etc/sudoers.d/50_vdsm_hook_vhostmd

clean:
	$(RM) *~ *.pyc
