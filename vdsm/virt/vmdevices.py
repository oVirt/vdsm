#
# Copyright 2014 Red Hat, Inc.
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

import caps

from . import vmxml


class Base(vmxml.Device):
    __slots__ = ('deviceType', 'device', 'alias', 'specParams', 'deviceId',
                 'conf', 'log', '_deviceXML', 'type', 'custom')

    def __init__(self, conf, log, **kwargs):
        self.conf = conf
        self.log = log
        self.specParams = {}
        self.custom = kwargs.pop('custom', {})
        for attr, value in kwargs.iteritems():
            try:
                setattr(self, attr, value)
            except AttributeError:  # skip read-only properties
                self.log.debug('Ignoring param (%s, %s) in %s', attr, value,
                               self.__class__.__name__)
        self._deviceXML = None

    def __str__(self):
        attrs = [':'.join((a, str(getattr(self, a, None)))) for a in dir(self)
                 if not a.startswith('__')]
        return ' '.join(attrs)


class Generic(Base):

    def getXML(self):
        """
        Create domxml for general device
        """
        return self.createXmlElem(self.type, self.device, ['address'])


class Console(Base):
    __slots__ = ()

    def getXML(self):
        """
        Create domxml for a console device.

        <console type='pty'>
          <target type='virtio' port='0'/>
        </console>
        """
        m = self.createXmlElem('console', 'pty')
        m.appendChildWithArgs('target', type='virtio', port='0')
        return m


class Controller(Base):
    __slots__ = ('address', 'model', 'index', 'master')

    def getXML(self):
        """
        Create domxml for controller device
        """
        ctrl = self.createXmlElem('controller', self.device,
                                  ['index', 'model', 'master', 'address'])
        if self.device == 'virtio-serial':
            ctrl.setAttrs(index='0', ports='16')

        return ctrl


class Smartcard(Base):
    __slots__ = ('address',)

    def getXML(self):
        """
        Add smartcard section to domain xml

        <smartcard mode='passthrough' type='spicevmc'>
          <address ... />
        </smartcard>
        """
        card = self.createXmlElem(self.device, None, ['address'])
        sourceAttrs = {'mode': self.specParams['mode']}
        if sourceAttrs['mode'] != 'host':
            sourceAttrs['type'] = self.specParams['type']
        card.setAttrs(**sourceAttrs)
        return card


class Sound(Base):
    __slots__ = ('address', 'alias')

    def getXML(self):
        """
        Create domxml for sound device
        """
        sound = self.createXmlElem('sound', None, ['address'])
        sound.setAttrs(model=self.device)
        return sound


class VideoDevice(Base):
    def getXML(self):
        """
        Create domxml for video device
        """
        video = self.createXmlElem('video', None, ['address'])
        sourceAttrs = {'vram': self.specParams.get('vram', '32768'),
                       'heads': self.specParams.get('heads', '1')}
        if 'ram' in self.specParams:
            sourceAttrs['ram'] = self.specParams['ram']

        video.appendChildWithArgs('model', type=self.device, **sourceAttrs)
        return video


class Redir(Base):
    __slots__ = ('address',)

    def getXML(self):
        """
        Create domxml for a redir device.
        <redirdev bus='usb' type='spicevmc'>
          <address type='usb' bus='0' port='1'/>
        </redirdev>
        """
        return self.createXmlElem('redirdev', self.device, ['bus', 'address'])


class Rng(Base):
    def getXML(self):
        """
        <rng model='virtio'>
            <rate period="2000" bytes="1234"/>
            <backend model='random'>/dev/random</backend>
        </rng>
        """
        rng = self.createXmlElem('rng', None, ['model'])

        # <rate... /> element
        if 'bytes' in self.specParams:
            rateAttrs = {'bytes': self.specParams['bytes']}
            if 'period' in self.specParams:
                rateAttrs['period'] = self.specParams['period']

            rng.appendChildWithArgs('rate', None, **rateAttrs)

        # <backend... /> element
        rng.appendChildWithArgs('backend',
                                caps.RNG_SOURCES[self.specParams['source']],
                                model='random')

        return rng


class Tpm(Base):
    __slots__ = ()

    def getXML(self):
        """
        Add tpm section to domain xml

        <tpm model='tpm-tis'>
            <backend type='passthrough'>
                <device path='/dev/tpm0'>
            </backend>
        </tpm>
        """
        tpm = self.createXmlElem(self.device, None)
        tpm.setAttrs(model=self.specParams['model'])
        backend = tpm.appendChildWithArgs('backend',
                                          type=self.specParams['mode'])
        backend.appendChildWithArgs('device',
                                    path=self.specParams['path'])
        return tpm
