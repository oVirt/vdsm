VDSMREGDIR=/usr/share/vdsm-reg
ETC=/etc
CONFDIR=$(ETC)/vdsm-reg
LOGDIR=/var/log/vdsm-reg
VDSMRUNDIR=/var/run/vdsm
MANDIR=/usr/share/man

all:
	@echo only \"make install\" is available
	@false

fixpaths:
	sed -i "s:@VDSMRUNDIR@:$(VDSMRUNDIR):" vdsm-reg.conf

install: fixpaths
	mkdir -p $(PREFIX)$(CONFDIR)
	mkdir -p $(PREFIX)$(VDSMREGDIR)
	mkdir -p $(PREFIX)$(ETC)/ovirt-config-setup.d/
	mkdir -p $(PREFIX)$(ETC)/ovirt-config-boot.d/
	mkdir -p $(PREFIX)$(LOGDIR)
	
	cp -p vdsm-reg $(PREFIX)$(ETC)/init.d
	cp -p {logger.conf,vdsm-reg.conf} $(PREFIX)$(CONFDIR)
	cp -p define.py config.py config-rhev-manager save-config deployUtil.py createDaemon.py $(PREFIX)$(VDSMREGDIR)
	cp -p vdsm-reg-setup.py $(PREFIX)$(VDSMREGDIR)/vdsm-reg-setup
	cp -p vdsm-gen-cert.py $(PREFIX)$(VDSMREGDIR)/vdsm-gen-cert
	cp -p vdsm-complete.py $(PREFIX)$(VDSMREGDIR)/vdsm-complete
	cp -p vdsm-upgrade.py $(PREFIX)$(VDSMREGDIR)/vdsm-upgrade
	ln -s $(VDSMREGDIR)/config-rhev-manager $(PREFIX)$(ETC)/ovirt-config-setup.d/"40_Configure the host for RHEV"
	cp -p vdsm-config $(PREFIX)$(ETC)/ovirt-config-boot.d
	cp -p vdsm-reg-logrotate.conf $(PREFIX)$(ETC)/logrotate.d/vdsm-reg
	cp -p vdsm-reg-logrotate $(PREFIX)$(ETC)/cron.hourly
	mkdir -p $(PREFIX)$(MANDIR)/man8
	cp -p vdsm-reg.8 $(PREFIX)$(MANDIR)/man8/
