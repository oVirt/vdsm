LIBEXECDIR=/usr/libexec/vdsm
HOOKSDIR=$(LIBEXECDIR)/hooks

all:

install:
	mkdir -p $(PREFIX)$(HOOKSDIR)/{before_vm_start,after_vm_destroy}
	cp before_vm_start.py $(PREFIX)$(HOOKSDIR)/before_vm_start/50_vhostmd
	ln -s ../before_vm_start/50_vhostmd $(PREFIX)$(HOOKSDIR)/before_vm_migrate_destination/50_vhostmd
	ln -s ../before_vm_start/50_vhostmd $(PREFIX)$(HOOKSDIR)/before_vm_dehibernate/50_vhostmd
	cp after_vm_destroy.py $(PREFIX)$(HOOKSDIR)/after_vm_destroy/50_vhostmd
	mkdir -p $(PREFIX)/etc/sudoers.d/
	cp sudoers.vdsm_hook_vhostmd $(PREFIX)/etc/sudoers.d/50_vdsm_hook_vhostmd

clean:
	$(RM) *~ *.pyc
