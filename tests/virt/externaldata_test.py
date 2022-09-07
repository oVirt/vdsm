# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
