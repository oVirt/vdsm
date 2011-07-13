INSTALL=install
LN_S=ln -s
VDSMREGDIR=/usr/share/vdsm-reg
ETC=/etc
REGCONFDIR=$(ETC)/vdsm-reg
VDSMREGLOGDIR=/var/log/vdsm-reg
VDSMRUNDIR=/var/run/vdsm
MANDIR=/usr/share/man

FILES=define.py config.py deployUtil.py createDaemon.py

all:
	@echo only \"make install\" is available
	@false

fixpaths:
	sed -i "s:@VDSMRUNDIR@:$(VDSMRUNDIR):" vdsm-reg.conf

install: fixpaths
	$(INSTALL) -Dd $(DESTDIR)$(REGCONFDIR)
	$(INSTALL) -Dd $(DESTDIR)$(VDSMREGDIR)
	$(INSTALL) -Dd $(DESTDIR)$(VDSMREGLOGDIR)
	$(INSTALL) -Dd $(DESTDIR)$(OVIRT_CONFIG_SETUP)
	$(INSTALL) -Dm 755 vdsm-reg $(DESTDIR)$(ETC)/init.d/vdsm-reg
	$(INSTALL) -Dm 644 logger.conf $(DESTDIR)$(REGCONFDIR)/logger.conf
	$(INSTALL) -Dm 644 vdsm-reg.conf $(DESTDIR)$(REGCONFDIR)/vdsm-reg.conf
	$(INSTALL) -Dm 644 $(FILES) $(DESTDIR)$(VDSMREGDIR)
	$(INSTALL) -Dm 755 save-config $(DESTDIR)$(VDSMREGDIR)
	$(INSTALL) -Dm 755 vdsm-reg-setup.py $(DESTDIR)$(VDSMREGDIR)/vdsm-reg-setup
	$(INSTALL) -Dm 755 vdsm-gen-cert.py $(DESTDIR)$(VDSMREGDIR)/vdsm-gen-cert
	$(INSTALL) -Dm 755 vdsm-complete.py $(DESTDIR)$(VDSMREGDIR)/vdsm-complete
	$(INSTALL) -Dm 755 vdsm-upgrade.py $(DESTDIR)$(VDSMREGDIR)/vdsm-upgrade
	$(INSTALL) -Dd $(DESTDIR)$(ETC)/ovirt-config-setup.d
	$(INSTALL) -Dm 755 vdsm-config $(DESTDIR)$(ETC)/ovirt-config-boot.d/vdsm-config
	$(INSTALL) -Dm 644 vdsm-reg-logrotate.conf $(DESTDIR)$(ETC)/logrotate.d/vdsm-reg
	$(INSTALL) -Dm 755 vdsm-reg-logrotate $(DESTDIR)$(ETC)/cron.hourly
	$(INSTALL) -Dm 644 vdsm-reg.8 $(DESTDIR)$(MANDIR)/man8/vdsm-reg.8
	$(INSTALL) -Dm 644 rhevm.py $(DESTDIR)$(OVIRT_CONFIG_SETUP)/rhevm.py
