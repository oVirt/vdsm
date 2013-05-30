# Copyright 2013 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#


from ipwrapper import routeAdd
from ipwrapper import routeDel
from ipwrapper import ruleAdd
from ipwrapper import ruleDel


class Iproute2(object):
    @staticmethod
    def configureSourceRoute(sourceRoute, device):
        for route in sourceRoute.routes:
            routeAdd(route)

        for rule in sourceRoute.rules:
            ruleAdd(rule)

    @staticmethod
    def removeSourceRoute(sourceRoute, device):
        for route in sourceRoute.routes:
            routeDel(route)

        for rule in sourceRoute.rules:
            ruleDel(rule)
