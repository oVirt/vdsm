
# Copyright 2013 Miguel Angel Ajo
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import errno
import os
import tempfile

from functools import wraps
from vdsm import constants
from vdsm import utils


def _createHookScript(hook_path, hook_filename, script=None):

    """ Puts a script in place to be executed by a hook, the script
        parameter must have a %(cookiefile)s placeholder for the output
        file that test will check later """

    hook_script_path = hook_path + '/' + hook_filename
    hook_script_cookiefile = tempfile.mktemp()

    with open(hook_script_path, 'w') as f:
        if script is None:
            script = "#!/bin/sh\ndate --rfc-3339=ns > %(cookiefile)s\n"

        f.write(script % {'cookiefile': hook_script_cookiefile})

    os.chmod(hook_script_path, 0o777)

    return hook_script_cookiefile


def ValidatesHook(hook_dir, hook_name, functional=True, hook_script=None):
    """ Decorator for test cases that need to validate hook point execution """
    def decorator(test_function):
        @wraps(test_function)
        def wrapper(*args, **kwargs):

            directory_existed = False

            if not functional:
                old_vdsm_hooks = constants.P_VDSM_HOOKS
                constants.P_VDSM_HOOKS = tempfile.mkdtemp()

            hook_path = constants.P_VDSM_HOOKS + '/' + hook_dir

            try:
                os.mkdir(hook_path)
            except OSError as mkdir_error:
                if mkdir_error.errno == errno.EEXIST:
                    directory_existed = True
                else:
                    raise

            cookie_file = _createHookScript(hook_path, hook_name, hook_script)

            output = None

            try:
                kwargs['hook_cookiefile'] = cookie_file
                output = test_function(*args, **kwargs)
            finally:
                if directory_existed:
                    utils.rmFile(hook_path + '/' + hook_name)
                else:
                    utils.rmTree(hook_path)

                utils.rmFile(cookie_file)

                if not functional:
                    constants.P_VDSM_HOOKS = old_vdsm_hooks

            return output

        return wrapper

    return decorator
