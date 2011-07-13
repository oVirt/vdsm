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
	mkdir -p $(DESTDIR)$(CONFDIR)
	mkdir -p $(DESTDIR)$(VDSMREGDIR)
	mkdir -p $(DESTDIR)$(ETC)/ovirt-config-setup.d/
	mkdir -p $(DESTDIR)$(ETC)/ovirt-config-boot.d/
	mkdir -p $(DESTDIR)$(LOGDIR)
	mkdir -p $(DESTDIR)$(OVIRT_CONFIG_SETUP)
	
	cp -p vdsm-reg $(DESTDIR)$(ETC)/init.d
	cp -p {logger.conf,vdsm-reg.conf} $(DESTDIR)$(CONFDIR)
	cp -p define.py config.py save-config deployUtil.py createDaemon.py $(DESTDIR)$(VDSMREGDIR)
	cp -p vdsm-reg-setup.py $(DESTDIR)$(VDSMREGDIR)/vdsm-reg-setup
	cp -p vdsm-gen-cert.py $(DESTDIR)$(VDSMREGDIR)/vdsm-gen-cert
	cp -p vdsm-complete.py $(DESTDIR)$(VDSMREGDIR)/vdsm-complete
	cp -p vdsm-upgrade.py $(DESTDIR)$(VDSMREGDIR)/vdsm-upgrade
	cp -p vdsm-config $(DESTDIR)$(ETC)/ovirt-config-boot.d
	cp -p vdsm-reg-logrotate.conf $(DESTDIR)$(ETC)/logrotate.d/vdsm-reg
	cp -p vdsm-reg-logrotate $(DESTDIR)$(ETC)/cron.hourly
	mkdir -p $(DESTDIR)$(MANDIR)/man8
	cp -p vdsm-reg.8 $(DESTDIR)$(MANDIR)/man8/
	cp -p rhevm.py $(DESTDIR)$(OVIRT_CONFIG_SETUP)
