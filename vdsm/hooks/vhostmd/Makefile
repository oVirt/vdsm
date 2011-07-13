LIBEXECDIR=/usr/libexec/vdsm
HOOKSDIR=$(LIBEXECDIR)/hooks

all:

install:
	mkdir -p $(DESTDIR)$(HOOKSDIR)/{before_vm_start,after_vm_destroy}
	cp before_vm_start.py $(DESTDIR)$(HOOKSDIR)/before_vm_start/50_vhostmd
	ln -s ../before_vm_start/50_vhostmd $(DESTDIR)$(HOOKSDIR)/before_vm_migrate_destination/50_vhostmd
	ln -s ../before_vm_start/50_vhostmd $(DESTDIR)$(HOOKSDIR)/before_vm_dehibernate/50_vhostmd
	cp after_vm_destroy.py $(DESTDIR)$(HOOKSDIR)/after_vm_destroy/50_vhostmd
	mkdir -p $(DESTDIR)/etc/sudoers.d/
	cp sudoers.vdsm_hook_vhostmd $(DESTDIR)/etc/sudoers.d/50_vdsm_hook_vhostmd

clean:
	$(RM) *~ *.pyc
