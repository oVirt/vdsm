# Copyright 2021 Red Hat, Inc.
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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Refer to the README and COPYING files for full details of the license
#

import logging

from vdsm.virt.externaldata import ExternalData


class TestExternalData(object):

    def test_hash(self):
        """ Make sure secure_hash() is not just plain hash() """
        assert hash("abc") != ExternalData.secure_hash("abc")

    def test_update(self):
        data_content = 'abc'
        timestamp = 1

        def read_function(last_modified):
            return data_content, timestamp

        # Init
        original_data = data_content
        original_hash = ExternalData.secure_hash(data_content)
        external = ExternalData("test", logging.getLogger(), read_function,
                                data_content, original_hash)
        data = external.data
        assert data.stable_data == data_content
        assert data.engine_hash == original_hash
        # Change 1 -- new (unstable) data
        data_content = 'def'
        timestamp = 2
        previous_data = data
        external.update()
        data = external.data
        assert data.stable_data == original_data
        assert data.engine_hash == original_hash
        assert data.monitor_hash != previous_data.monitor_hash
        # Change 1 -- stable data
        previous_data = data
        external.update()
        data = external.data
        assert data.stable_data == data_content
        assert data.monitor_hash == previous_data.monitor_hash
        assert data.engine_hash != original_hash
        # No change
        previous_data = data
        external.update()
        data = external.data
        assert data == previous_data
        # Change 2 -- new (unstable) data
        data_content = 'ghi'
        timestamp = 3
        previous_data = data
        external.update()
        data = external.data
        assert data.stable_data == previous_data.stable_data
        assert data.monitor_hash != previous_data.monitor_hash

    def test_force_update(self):
        data_content = 'abc'
        timestamp = 1

        def read_function(last_modified):
            return data_content, timestamp

        # Init
        original_hash = ExternalData.secure_hash(data_content)
        external = ExternalData("test", logging.getLogger(), read_function,
                                data_content, original_hash)
        data = external.data
        assert data.stable_data == data_content
        assert data.engine_hash == original_hash
        # Force update
        data_content = 'def'
        timestamp = 2
        previous_data = data
        external.update(force=True)
        data = external.data
        assert data.stable_data == data_content
        assert data.engine_hash != previous_data.engine_hash
        assert data.monitor_hash != previous_data.monitor_hash
