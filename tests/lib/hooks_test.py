# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import hashlib
import itertools
import libvirt
import logging
import textwrap
import os
import os.path
import pickle
import pytest
import sys

from collections import namedtuple

from vdsm.common import exception
from vdsm.common import hooks


def on_ascii_locale():
    locale = sys.getfilesystemencoding().upper()
    return locale == "ASCII" or locale == "ANSI_X3.4-1968"


DirEntry = namedtuple("DirEntry", "name, mode, contents")
FileEntry = namedtuple("FileEntry", "name, mode, contents")


def dir_entry_apply(self, hooks_dir):
    path = hooks_dir.join(self.name)
    path.mkdir()
    for entry in self.contents:
        entry.apply(path)
    path.chmod(self.mode)


def file_entry_apply(self, hooks_dir):
    path = hooks_dir.join(self.name)
    path.write(self.contents)
    path.chmod(self.mode)


DirEntry.apply = dir_entry_apply
FileEntry.apply = file_entry_apply


@pytest.fixture
def fake_hooks_root(monkeypatch, tmpdir, request):
    entries = getattr(request, 'param', [])
    for entry in entries:
        entry.apply(tmpdir)
    with monkeypatch.context() as m:
        m.setattr(hooks, "P_VDSM_HOOKS", str(tmpdir) + "/")
        yield tmpdir


@pytest.fixture
def hooks_dir(fake_hooks_root, request):
    hooks_dir = fake_hooks_root.mkdir("hooks_dir")
    entries = getattr(request, 'param', [])
    for entry in entries:
        entry.apply(hooks_dir)
    yield hooks_dir


@pytest.mark.parametrize("hooks_dir", indirect=["hooks_dir"], argvalues=[
    pytest.param(
        [
            FileEntry("executable", 0o700, ""),
            FileEntry("executable_2", 0o700, ""),
        ],
        id="two executable scripts"
    ),
])
def test_scripts_per_dir_should_list_scripts(hooks_dir):
    scripts = hooks._scriptsPerDir(hooks_dir.basename)

    assert len(scripts) == 2
    assert sorted(scripts) == sorted(list(str(p) for p in hooks_dir.visit()))


@pytest.mark.parametrize("hooks_dir", indirect=True, argvalues=[
    pytest.param(
        [
            FileEntry("non-executable", 0o666, ""),
        ],
        id="non-executable"
    ),
    pytest.param(
        [
            DirEntry("__pycache__", 0o777, []),
        ],
        id="executable directory"
    ),
    pytest.param(
        [
            DirEntry("nested", 0o777, [
                FileEntry("executable", 0o777, "")
            ])
        ],
        id="script in nested dir"
    ),
])
def test_scripts_per_dir_should_not_list(hooks_dir):
    assert hooks._scriptsPerDir(hooks_dir.basename) == []


@pytest.mark.parametrize("dir_name, error", [
    pytest.param(
        "/tmp/evil/absolute/path",
        "Cannot use absolute path as hook directory",
        id="absolute path"
    ),
    pytest.param(
        "../../tmp/evil/relative/path",
        "Hook directory paths cannot contain '..'",
        id="escaping relative path"
    ),
])
def test_scripts_per_dir_should_raise(fake_hooks_root, dir_name, error):
    with pytest.raises(ValueError) as e:
        hooks._scriptsPerDir(dir_name)

    assert error in str(e.value)


@pytest.mark.parametrize("hooks_dir", indirect=["hooks_dir"], argvalues=[
    pytest.param(
        [
            FileEntry("executable", 0o700, ""),
        ],
        id="no trailing slash"
    ),
])
def test_scripts_per_dir_should_accept_root_without_trailing_slash(monkeypatch,
                                                                   hooks_dir):
    with monkeypatch.context() as m:
        hooks_root = hooks.P_VDSM_HOOKS.rstrip("/")
        m.setattr(hooks, "P_VDSM_HOOKS", hooks_root)
        scripts = hooks._scriptsPerDir(hooks_dir.basename)

        assert len(scripts) == 1


def test_rhd_should_return_unmodified_data_when_no_hooks(hooks_dir):
    assert hooks._runHooksDir(u"algo", hooks_dir.basename) == u"algo"


@pytest.fixture
def dummy_hook(hooks_dir):
    FileEntry("hook.sh", 0o755, "#!/bin/bash").apply(hooks_dir)
    yield


@pytest.mark.parametrize("data, expected", [
    pytest.param(
        None,
        u"",
        id="no XML data"
    ),
    pytest.param(
        u"",
        u"",
        id="empty XML data"
    ),
    pytest.param(
        u"<abc>def</abc>",
        u"<abc>def</abc>",
        id="simple XML data"
    ),
])
def test_rhd_should_handle_xml_data(dummy_hook, hooks_dir, data, expected):
    result = hooks._runHooksDir(data, hooks_dir.basename,
                                hookType=hooks._DOMXML_HOOK)
    assert result == expected


@pytest.mark.parametrize("data, expected", [
    pytest.param(
        None,
        None,
        id="no JSON data"
    ),
    pytest.param(
        {"abc": "def"},
        {"abc": "def"},
        id="simple JSON data"
    ),
    pytest.param(
        {"key": b"\xc4\x85b\xc4\x87".decode("utf-8")},
        {"key": b"\xc4\x85b\xc4\x87".decode("utf-8")},
        id="JSON data with localized chars"
    ),
])
def test_rhd_should_handle_json_data(dummy_hook, hooks_dir, data, expected):
    result = hooks._runHooksDir(data, hooks_dir.basename,
                                hookType=hooks._JSON_HOOK)
    assert result == expected


def appender_script(script_name, exit_code=0):
    code = textwrap.dedent(
        """\
        #!/bin/bash
        myname="$(basename "$0")"
        echo "$myname" >> "$_hook_domxml"
        >&2 echo "$myname"
        exit {exit_code}
        """.format(exit_code=exit_code))
    return FileEntry(script_name, 0o777, code)


@pytest.mark.parametrize("hooks_dir", indirect=True, argvalues=[
    pytest.param(
        [
            appender_script("myhook.sh"),
        ],
        id="single hook"
    ),
])
def test_rhd_should_run_a_hook(hooks_dir):
    assert hooks._runHooksDir(u"123", hooks_dir.basename) == u"123myhook.sh\n"


@pytest.mark.parametrize("hooks_dir", indirect=True, argvalues=[
    pytest.param(
        perm,
        id="-".join(script.name for script in perm)
    ) for perm in itertools.permutations([
        appender_script("1.sh"),
        appender_script("2.sh"),
        appender_script("3.sh")
    ])
])
def test_rhd_should_run_hooks_in_order(hooks_dir):
    assert hooks._runHooksDir(u"", hooks_dir.basename) == u"1.sh\n2.sh\n3.sh\n"


@pytest.mark.parametrize("hooks_dir,error", indirect=["hooks_dir"], argvalues=[
    pytest.param(
        [
            appender_script("1.sh"),
            appender_script("2.sh", exit_code=1),
            appender_script("3.sh", exit_code=1)
        ],
        ["2.sh", "3.sh"],
        id="non-fatal hook errors",
        marks=pytest.mark.xfail(reason="doesn't include all 'err's")
    ),
    pytest.param(
        [
            appender_script("1.sh"),
            appender_script("2.sh", exit_code=2),
            appender_script("3.sh")
        ],
        ["2.sh"],
        id="fatal hook error, '3.sh' skipped"
    ),
])
def test_rhd_should_raise_hook_errors(hooks_dir, error):
    with pytest.raises(exception.HookError) as e:
        hooks._runHooksDir(u"", hooks_dir.basename)

    for err in error:
        assert err in str(e.value)


@pytest.mark.parametrize("hooks_dir,expected", indirect=["hooks_dir"],
                         argvalues=[
    pytest.param(
        [
            appender_script("1.sh"),
            appender_script("2.sh", exit_code=1),
            appender_script("3.sh")
        ],
        u"1.sh\n2.sh\n3.sh\n",
        id="non-fatal hook error"
    ),
    pytest.param(
        [
            appender_script("1.sh"),
            appender_script("2.sh", exit_code=2),
            appender_script("3.sh")
        ],
        u"1.sh\n2.sh\n",
        id="fatal hook error, '3.sh' skipped"
    ),
])
def test_rhd_should_handle_hook_errors(hooks_dir, expected):
    assert hooks._runHooksDir(u"", hooks_dir.basename, raiseError=False) == \
        expected


@pytest.mark.parametrize("hooks_dir", indirect=True, argvalues=[
    pytest.param(
        [
            appender_script("1.sh", exit_code=111),
        ],
        id="invalid exit code"
    ),
])
def test_rhd_should_report_invalid_hook_error_codes(caplog, hooks_dir):
    hooks._runHooksDir(u"", hooks_dir.basename, raiseError=False)

    assert "111" in "".join(msg
                            for (_, lvl, msg) in caplog.record_tuples
                            if lvl == logging.WARNING)


@pytest.mark.parametrize("hooks_dir", indirect=True, argvalues=[
    pytest.param(
        [
            appender_script("1.sh"),
        ],
        id="script writing to stderr"
    ),
])
def test_rhd_should_report_hook_stderr(caplog, hooks_dir):
    caplog.set_level(logging.INFO)
    hooks._runHooksDir(u"", hooks_dir.basename)

    assert "1.sh" in "".join(msg
                             for _, lvl, msg in caplog.record_tuples
                             if lvl == logging.INFO)


@pytest.fixture
def env_dump(hooks_dir):
    dump_path = str(hooks_dir.join("env_dump.pickle"))
    code = textwrap.dedent(
        """\
        #!{}
        import os
        import pickle
        import six
        import sys

        with open("{}", "wb") as dump_file:
            env = dict()
            for k, v in os.environ.items():
                if six.PY2:
                    v = v.decode(sys.getfilesystemencoding())
                env[k] = v
            pickle.dump(env, dump_file)
        """).format(sys.executable, dump_path)
    FileEntry("env_dump.py", 0o755, code).apply(hooks_dir)
    yield dump_path


@pytest.mark.parametrize("vmconf, params, expected", [
    pytest.param(
        {},
        {"abc": "def"},
        {"abc": "def"},
        id="simple variable"
    ),
    pytest.param(
        {},
        {"abc": b"\xc4\x85b\xc4\x87".decode("utf-8")},
        {"abc": b"\xc4\x85b\xc4\x87".decode("utf-8")},
        id="variable with local chars",
        marks=pytest.mark.xfail(on_ascii_locale(),
                                reason="no support for localized chars")
    ),
    pytest.param(
        {},
        {"abc": u"\udcfc"},
        {},
        id="variable with invalid utf-8 should be ignored"
    ),
    pytest.param(
        {"vmId": "myvm"},
        {},
        {"vmId": "myvm"},
        id="VM id"
    ),
    pytest.param(
        {"custom": {"abc": "def"}},
        {},
        {"abc": "def"},
        id="vmconf param"
    ),
    pytest.param(
        {"custom": {"abc": "geh"}},
        {"abc": "def"},
        {"abc": "geh"},
        id="vmconf param override"
    ),
])
def test_rhd_should_assemble_environment_for_hooks(hooks_dir, env_dump, vmconf,
                                                   params, expected):
    hooks._runHooksDir(u"", hooks_dir.basename, vmconf, params=params)
    with open(env_dump, "rb") as f:
        env = pickle.load(f)

    for k, v in expected.items():
        assert env[k] == v


@pytest.fixture
def mkstemp_path(monkeypatch, hooks_dir):
    with monkeypatch.context() as m:
        tmp_path = str(hooks_dir.join("tmp_file"))

        def impl():
            return os.open(tmp_path, os.O_RDWR | os.O_CREAT, 0o600), tmp_path

        m.setattr(hooks.tempfile, 'mkstemp', impl)
        yield tmp_path


@pytest.mark.parametrize("var_name, hook_type", [
    pytest.param(
        "_hook_domxml",
        hooks._DOMXML_HOOK,
        id="xml hook"
    ),
    pytest.param(
        "_hook_json",
        hooks._JSON_HOOK,
        id="JSON hook"
    )
])
def test_rhd_should_pass_data_file_to_hooks(hooks_dir, env_dump, mkstemp_path,
                                            var_name, hook_type):
    hooks._runHooksDir(None, hooks_dir.basename, hookType=hook_type)
    with open(env_dump, "rb") as f:
        env = pickle.load(f)

    assert env[var_name] == mkstemp_path


@pytest.fixture
def hooking_client(hooks_dir):
    code = textwrap.dedent(
        """\
        #!{}
        import sys

        try:
            import hooking
        except ImportError:
            sys.exit(2)
        """).format(sys.executable)
    FileEntry("hook_client.py", 0o755, code).apply(hooks_dir)
    yield


def test_rhd_should_make_import_hooking_possible(hooks_dir, hooking_client):
    hooks._runHooksDir(u"", hooks_dir.basename)


@pytest.mark.parametrize("hooks_dir, expected", indirect=["hooks_dir"],
                         argvalues=[
    pytest.param(
        [
            FileEntry("script.sh", 0o777, "abc")
        ],
        hashlib.sha256(b"abc").hexdigest(),
        id="simple script"
    ),
    pytest.param(
        [],
        "",
        id="non-existent script"
    ),
])
def test_get_script_info_should_return_checksum(hooks_dir, expected):
    path = str(hooks_dir.join("script.sh"))

    assert hooks._getScriptInfo(path) == {"checksum": expected}


@pytest.mark.parametrize("hooks_dir, expected", indirect=["hooks_dir"],
                         argvalues=[
    pytest.param(
        [
            FileEntry("script.sh", 0o777, "abc"),
            FileEntry("script2.sh", 0o777, "def")
        ],
        {
            "script.sh": {"checksum": hashlib.sha256(b"abc").hexdigest()},
            "script2.sh": {"checksum": hashlib.sha256(b"def").hexdigest()},
        },
        id="some scripts"
    ),
    pytest.param(
        [],
        {},
        id="no scripts"
    ),
])
def test_get_hook_info_should_return_info(hooks_dir, expected):
    assert hooks._getHookInfo(hooks_dir.basename) == expected


@pytest.mark.parametrize("fake_hooks_root, expected",
                         indirect=["fake_hooks_root"],
                         argvalues=[
    pytest.param(  # noqa: E122
        [
            DirEntry("after_vm_smth", 0o777, [
                FileEntry("script1.py", 0o777, "abc"),
                FileEntry("script2.py", 0o777, "def"),
                FileEntry("non-script", 0o666, "kkk")
            ]),
            DirEntry("before_vm_smth", 0o777, [
                FileEntry("script3.py", 0o777, "xyz")
            ]),
            DirEntry("empty_hook", 0o777, [])
        ],
        {
            "after_vm_smth": {
                "script1.py": {
                    "checksum": hashlib.sha256(b"abc").hexdigest()
                },
                "script2.py": {
                    "checksum": hashlib.sha256(b"def").hexdigest()
                }
            },
            "before_vm_smth": {
                "script3.py": {
                    "checksum": hashlib.sha256(b"xyz").hexdigest()
                }
            },
        },
        id="example hooks"
    ),
])  # noqa: E122
def test_installed_should_return_hooks_info(fake_hooks_root, expected):
    assert hooks.installed() == expected


@pytest.fixture
def launch_flags_path(monkeypatch, tmpdir):
    lfp = hooks._LAUNCH_FLAGS_PATH
    if os.path.isabs(lfp):
        lfp = lfp[1:]
    new_lfp = os.path.join(str(tmpdir), lfp)
    with monkeypatch.context() as m:
        m.setattr(hooks, "_LAUNCH_FLAGS_PATH", new_lfp)
        yield new_lfp


@pytest.fixture
def vm_id():
    yield "my-vm-id"


@pytest.fixture
def flag_file(launch_flags_path, vm_id):
    yield hooks._LAUNCH_FLAGS_PATH % vm_id


@pytest.mark.parametrize("flag", [
    pytest.param(
        libvirt.VIR_DOMAIN_NONE,
        id="libvirt.VIR_DOMAIN_NONE"
    ),
    pytest.param(
        libvirt.VIR_DOMAIN_START_PAUSED,
        id="libvirt.VIR_DOMAIN_START_PAUSED"
    )
])
def test_vm_launch_flags(flag_file, flag, vm_id):
    assert not os.path.exists(flag_file)

    hooks.dump_vm_launch_flags_to_file(vm_id, flag)

    assert hooks.load_vm_launch_flags_from_file(vm_id) == flag
    assert os.path.exists(flag_file)

    hooks.remove_vm_launch_flags_file(vm_id)

    assert not os.path.exists(flag_file)
