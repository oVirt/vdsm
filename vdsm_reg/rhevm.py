#!/usr/bin/python
#
# Copyright 2011 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
# Written by Joey Boggs <jboggs@redhat.com>
#

import os
import sys
from ovirtnode.ovirtfunctions import ovirt_store_config, is_valid_host_or_ip, \
                                     is_valid_port, PluginBase, log, network_up
from snack import ButtonChoiceWindow, Entry, Grid, Label, Checkbox, \
                  FLAG_DISABLED, FLAGS_SET

sys.path.append('/usr/share/vdsm-reg')
import deployUtil

VDSM_CONFIG = "/etc/vdsm/vdsm.conf"
VDSM_REG_CONFIG = "/etc/vdsm-reg/vdsm-reg.conf"

fWriteConfig = 0
def set_defaults():
    vdsm_config_file = open(VDSM_CONFIG, "w")
    vdsm_config = """[vars]
trust_store_path = /etc/pki/vdsm/
ssl = true

[addresses]
management_port = 54321
"""
    vdsm_config_file.write(vdsm_config)
    vdsm_config_file.close()

def write_vdsm_config(rhevm_host, rhevm_port):
    if not os.path.exists(VDSM_CONFIG):
        os.system("touch " + VDSM_CONFIG)
    if os.path.getsize(VDSM_CONFIG) == 0:
        set_defaults()
        ovirt_store_config(VDSM_CONFIG)
        log("RHEV agent configuration files created.")
    else:
        log("RHEV agent configuration files already exist.")

    ret = os.system("ping -c 1 " + rhevm_host + " &> /dev/null")
    if ret == 0:
        sed_cmd = "sed -i --copy \"s/\(^vdc_host_name=\)\(..*$\)/vdc_host_name="+rhevm_host+"/\" " + VDSM_REG_CONFIG
        ret = os.system(sed_cmd)
        if ret == 0:
            log("The RHEV Manager's address is set.\n")
        if rhevm_port != "":
            sed_cmd = "sed -i --copy \"s/\(^vdc_host_port=\)\(..*$\)/vdc_host_port="+str(rhevm_port)+"/\" " + VDSM_REG_CONFIG
            os.system(sed_cmd)
            log("The RHEV Manager's port set.\n")
            fWriteConfig=1
    else:
        log("Either " + rhevm_host + " is an invalid address or the RHEV Manager unresponsive.\n")
        return False

    if fWriteConfig == 1:
        log("Saving vdsm-reg.conf\n")
        if ovirt_store_config(VDSM_REG_CONFIG):
            log("vdsm-reg.conf Saved\n")
            return True

def get_rhevm_config():
    vdsm_config = open(VDSM_REG_CONFIG)
    config = {}
    config["vdc_host_port"] = 443
    for line in vdsm_config:
        line = line.strip().replace(" ","").split("=")
        if "vdc_host_name" in line:
            item, config["vdc_host_name"] = line[0], line[1]
        if "vdc_host_port" in line:
            item, config["vdc_host_port"] = line[0], line[1]
    vdc_server = config["vdc_host_name"] + ":" + config["vdc_host_port"]
    vdsm_config.close()
    return vdc_server

class Plugin(PluginBase):
    """Plugin for RHEV-M configuration.
    """

    def __init__(self, ncs):
        PluginBase.__init__(self, "RHEV-M", ncs)

    def form(self):
        elements = Grid(2, 10)
        is_network_up = network_up()
        if is_network_up:
            header_message = "RHEV-M Configuration"
        else:
            header_message = "Network Down, RHEV-M Configuration Disabled"

        elements.setField(Label(header_message), 0, 0, anchorLeft = 1)
        elements.setField(Label(""), 0, 1, anchorLeft = 1)
        rhevm_grid = Grid(2,2)
        rhevm_grid.setField(Label("Management Server:"), 0, 0, anchorLeft = 1)
        self.rhevm_server = Entry(25, "")
        self.rhevm_server.setCallback(self.valid_rhevm_server_callback)
        rhevm_grid.setField(Label("Management Server Port:"), 0, 1, anchorLeft = 1)
        self.rhevm_server_port = Entry(6, "", scroll = 0)
        self.rhevm_server_port.setCallback(self.valid_rhevm_server_port_callback)
        rhevm_grid.setField(self.rhevm_server, 1, 0, anchorLeft = 1, padding=(2, 0, 0, 1))
        rhevm_grid.setField(self.rhevm_server_port, 1, 1, anchorLeft = 1, padding=(2, 0, 0, 1))
        elements.setField(rhevm_grid, 0, 2, anchorLeft = 1, padding = (0,1,0,0))
        self.verify_rhevm_cert = Checkbox("Verify RHEVM Certificate")
        elements.setField(self.verify_rhevm_cert, 0, 3, anchorLeft = 1, padding = (0,1,0,0))

        if not is_network_up:
            for field in self.rhevm_server, self.rhevm_server_port, self.verify_rhevm_cert:
                field.setFlags(FLAG_DISABLED, FLAGS_SET)

        try:
            rhevm_server = get_rhevm_config()
            rhevm_server,rhevm_port = rhevm_server.split(":")
            if rhevm_server.startswith("None"):
                self.rhevm_server.set("")
            else:
                self.rhevm_server.set(rhevm_server)
            self.rhevm_server_port.set(rhevm_port)

        except:
            pass
        return [Label(""), elements]

    def action(self):
        self.ncs.screen.setColor("BUTTON", "black", "red")
        self.ncs.screen.setColor("ACTBUTTON", "blue", "white")
        if len(self.rhevm_server.value()) > 0:
            if self.verify_rhevm_cert.selected():
                if deployUtil.getRhevmCert(self.rhevm_server.value(),  self.rhevm_server_port.value()):
                    path, dontCare = deployUtil.certPaths('')
                    fp = deployUtil.generateFingerPrint(path)
                    approval = ButtonChoiceWindow(self.ncs.screen,
                                "Certificate Fingerprint (Rejecting the finrgerprint will reboot the host):",
                                fp, buttons = ['Approve', 'Reject'])
                    if 'reject' == approval:
                        out, err, rc = deployUtil._logExec(['/sbin/reboot'])
                        if rc is not 0:
                            log("Failed rebooting after fingerprint" + \
                                "mismatch: %s", err)
                    else:
                        ovirt_store_config(path)
                        self.ncs.reset_screen_colors()
                else:
                    ButtonChoiceWindow(self.ncs.screen, "RHEV-M Configuration", "Failed downloading RHEV-M certificate", buttons = ['Ok'])
                    self.ncs.reset_screen_colors()
            if write_vdsm_config(self.rhevm_server.value(), self.rhevm_server_port.value()):
                ButtonChoiceWindow(self.ncs.screen, "RHEV-M Configuration", "RHEV-M Configuration Successfully Updated", buttons = ['Ok'])
                self.ncs.reset_screen_colors()
                return True
            else:
                ButtonChoiceWindow(self.ncs.screen, "RHEV-M Configuration", "RHEV-M Configuration Failed", buttons = ['Ok'])
                self.ncs.reset_screen_colors()
                return False

    def valid_rhevm_server_callback(self):
        if not is_valid_host_or_ip(self.rhevm_server.value()):
            self.ncs.screen.setColor("BUTTON", "black", "red")
            self.ncs.screen.setColor("ACTBUTTON", "blue", "white")
            ButtonChoiceWindow(self.ncs.screen, "Configuration Check", "Invalid RHEV-M Hostname or Address", buttons = ['Ok'])
            self.ncs.reset_screen_colors()


    def valid_rhevm_server_port_callback(self):
        if not is_valid_port(self.rhevm_server_port.value()):
            self.ncs.screen.setColor("BUTTON", "black", "red")
            self.ncs.screen.setColor("ACTBUTTON", "blue", "white")
            ButtonChoiceWindow(self.ncs.screen, "Configuration Check", "Invalid RHEV-M Server Port", buttons = ['Ok'])
            self.ncs.reset_screen_colors()

def get_plugin(ncs):
    return Plugin(ncs)
