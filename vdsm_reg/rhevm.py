#!/usr/bin/python
# rhevm.py - Copyright (C) 2011 Red Hat, Inc.
# Written by Joey Boggs <jboggs@redhat.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.  A copy of the GNU General Public License is
# also available at http://www.gnu.org/copyleft/gpl.html.

import os
import sys
from ovirtnode.ovirtfunctions import *
from subprocess import Popen, PIPE, STDOUT
from snack import *
import _snack

VDSM_CONFIG = "/etc/vdsm/vdsm.conf"
VDSM_REG_CONFIG = "/etc/vdsm-reg/vdsm-reg.conf"

fWriteConfig = 0
def set_defaults():
    vdsm_config_file = open(VDSM_CONFIG, "w")
    vdsm_config = """[vars]
trust_store_path = /etc/pki/vdsm/
ssl = true

[netconsole]
netconsole_enable = true

[addresses]
management_port = 54321
"""
    vdsm_config_file.write(vdsm_config)
    vdsm_config_file.close()

def write_vdsm_config(rhevm_host, netconsole_host):
    if not os.path.exists(VDSM_CONFIG):
        os.system("touch " + VDSM_CONFIG)
    if os.path.getsize(VDSM_CONFIG) == 0:
        set_defaults()
        ovirt_store_config(VDSM_CONFIG)
        log("RHEV agent configuration files created.")
    else:
        log("RHEV agent configuration files already exist.")
    try:
        rhevm_host, rhevm_port = rhevm_host.split(":")
    except ValueError, e:
        rhevm_port = 443

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

    try:
        netconsole_host, netconsole_port = netconsole_host.split(":")
    except ValueError, e:
        netconsole_port = 25825

    if netconsole_host != "":
        ret = os.system("ping -c 1 " + netconsole_host + " &> /dev/null")
        if ret == 0:
            sed_cmd = "sed -i --copy \"s/\(^nc_host_name=\)\(..*$\)/nc_host_name="+netconsole_host+"/\" /etc/vdsm-reg/vdsm-reg.conf"
            ret = os.system(sed_cmd)
            if ret == 0:
                log("The NetConsole server's address is set.\n")
            if netconsole_host != netconsole_port:
                sed_cmd = "sed -i --copy \"s/\(^nc_host_port=\)\(..*$\)/nc_host_port="+str(netconsole_port)+"/\" /etc/vdsm-reg/vdsm-reg.conf"
                ret = os.system(sed_cmd)
                if ret == 0:
                    log("The NetConsole server's port is set.\n")
                    fWriteConfig=1
        else:
            log("Either " + netconsole_host + " is an invalid address or the NetConsole server is unresponsive. Aborting configuration.\n")

    else:
        log("Skipping NetConsole configuration.\n")

    if fWriteConfig == 1:
        log("Saving vdsm-reg.conf\n")
        if ovirt_store_config(VDSM_REG_CONFIG):
            log("vdsm-reg.conf Saved\n")
            return True

def get_rhevm_config():
    vdsm_config = open(VDSM_REG_CONFIG)
    config = {}
    config["vdc_host_port"] = 443
    config["nc_host_port"] = 25825
    for line in vdsm_config:
        line = line.strip().replace(" ","").split("=")
        if "vdc_host_name" in line:
            item, config["vdc_host_name"] = line[0], line[1]
        if "vdc_host_port" in line:
            item, config["vdc_host_port"] = line[0], line[1]
        if "nc_host_name" in line:
            item, config["nc_host_name"] = line[0], line[1]
        if "nc_host_port" in line:
            item, config["nc_host_port"] = line[0], line[1]
    vdc_server = config["vdc_host_name"] + ":" + config["vdc_host_port"]
    nc_server = config["nc_host_name"] + ":" + config["nc_host_port"]
    vdsm_config.close()
    return (vdc_server, nc_server)

class Plugin(PluginBase):
    """Plugin for RHEV-M configuration.
    """

    def __init__(self, ncs):
        PluginBase.__init__(self, "RHEV-M", ncs)

    def form(self):
        elements = Grid(2, 10)
        elements.setField(Label("RHEV-M Configuration"), 0, 0, anchorLeft = 1)
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
        netconsole_grid = Grid(2,2)
        netconsole_grid.setField(Label("NetConsole Server:"), 0, 0, anchorLeft = 1)
        self.netconsole_server = Entry(25, "")
        self.netconsole_server.setCallback(self.valid_netconsole_server_callback)
        netconsole_grid.setField(Label("NetConsole Server Port:"), 0, 1, anchorLeft = 1)
        self.netconsole_server_port = Entry(6, "", scroll = 0)
        self.netconsole_server_port.setCallback(self.valid_netconsole_server_port_callback)
        netconsole_grid.setField(self.netconsole_server, 1, 0, anchorLeft = 1, padding=(2, 0, 0, 1))
        netconsole_grid.setField(self.netconsole_server_port, 1, 1, anchorLeft = 1, padding=(2, 0, 0, 1))
        elements.setField(rhevm_grid, 0, 3, anchorLeft = 1, padding = (0,1,0,0))
        elements.setField(netconsole_grid, 0, 4, anchorLeft = 1, padding = (0,1,0,0))
        try:
            rhevm_server, netconsole_server = get_rhevm_config()
            rhevm_server,rhevm_port = rhevm_server.split(":")
            netconsole_server,netconsole_server_port = netconsole_server.split(":")
            if rhevm_server.startswith("None"):
                self.rhevm_server.set("")
            else:
                self.rhevm_server.set(rhevm_server)
            self.rhevm_server_port.set(rhevm_port)

            if netconsole_server.startswith("None"):
                self.netconsole_server.set("")
            else:
                self.netconsole_server.set(netconsole_server)
            self.netconsole_server_port.set(netconsole_server_port)
        except:
            pass
        return [Label(""), elements]

    def action(self):
        self.ncs.screen.setColor("BUTTON", "black", "red")
        self.ncs.screen.setColor("ACTBUTTON", "blue", "white")
        if len(self.rhevm_server.value()) > 0  and len(self.netconsole_server.value()) > 0 :
            if write_vdsm_config(self.rhevm_server.value(), self.netconsole_server.value()):
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

    def valid_netconsole_server_callback(self):
        if not is_valid_host_or_ip(self.netconsole_server.value()):
            self.ncs.screen.setColor("BUTTON", "black", "red")
            self.ncs.screen.setColor("ACTBUTTON", "blue", "white")
            ButtonChoiceWindow(self.ncs.screen, "Configuration Check", "Invalid NetConsole Hostname or Address", buttons = ['Ok'])
            self.ncs.reset_screen_colors()

    def valid_netconsole_server_port_callback(self):
        if not is_valid_port(self.netconsole_server_port.value()):
            self.ncs.screen.setColor("BUTTON", "black", "red")
            self.ncs.screen.setColor("ACTBUTTON", "blue", "white")
            ButtonChoiceWindow(self.ncs.screen, "Configuration Check", "Invalid NetConsole Server Port", buttons = ['Ok'])
            self.ncs.reset_screen_colors()

def get_plugin(ncs):
    return Plugin(ncs)
