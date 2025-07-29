<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Development

## Environment setup

You can develop VDSM either locally on your machine or using a dev container. Choose the option that works best for your setup:

### Option 1: Dev Container (Recommended)

The easiest way to get started is using the provided dev container, which includes all dependencies pre-installed:

1. **Prerequisites**: Install VS Code with the Dev Containers extension
2. **Choose distribution** (optional): You can change the Linux distribution by modifying the `DISTRO` environment variable in `.devcontainer/devcontainer.json`:
   ```json
   "containerEnv": {
     "DISTRO": "centos-9"  // Options: centos-9, centos-10, alma-9
   }
   ```
3. **Open in container**: Use "Dev Containers: Open Folder in Container" from VS Code

In the dev container, the virtual environment is automatically created at `/venv` and VS Code is configured to use `/venv/bin/python` as the interpreter. The virtual environment is also automatically activated in your shell sessions via `.bashrc`, so you don't need to manually activate it. However, you can still activate it manually if needed:

    source /venv/bin/activate

### Option 2: Local Development

For local development on Fedora, CentOS, or RHEL:

Enable oVirt packages for Fedora:

    sudo dnf copr enable -y nsoffer/ioprocess-preview
    sudo dnf copr enable -y nsoffer/ovirt-imageio-preview

Enable
[virt-preview](https://copr.fedorainfracloud.org/coprs/g/virtmaint-sig/virt-preview/)
repository to obtain latest qemu and libvirt versions:

    sudo dnf copr enable @virtmaint-sig/virt-preview

Update the system after enabling all repositories:

    sudo dnf update -y

Fork the project on https://github.com/oVirt/vdsm.

Clone your fork:

    sudo dnf install -y git
    git clone git@github.com:{your_username}/vdsm.git

Install additional packages for Fedora, CentOS, and RHEL:

    contrib/install-pkg.sh

Generate the Makefile (and configure script):

    ./autogen.sh --system --enable-timestamp

### Virtual Environment Setup

The virtual environment setup depends on your development environment:

**In a dev container:**
The virtual environment is automatically created at `/venv` during container build. No additional setup is required.

**For local development:**
Create the virtual environment (https://docs.python.org/3/library/venv.html), which is necessary to run the tests later. This needs to be done only once:

    make venv


## Building Vdsm

Before building, it is recommended to recreate the Makefile because it
contains version numbers, which might have changed by updating the local
repository:

    ./autogen.sh --system --enable-timestamp

To build Vdsm:

    make

To create the RPMs:

    make rpm

To upgrade your system with local build's RPM (before you do this you should
activate maintenance mode for Vdsm):

    make upgrade


## Running the tests

To run tests, first enter the virtual environment:

**In a dev container:**

    source /venv/bin/activate

**For local development:**

    source ~/.venv/vdsm/bin/activate

Then start some tests with tox, for example the networking tests:

    tox -e network

To exit the virtual environment afterwards:

    deactivate

For more information about testing see [/tests/README.md](/tests/README.md).


## Making new releases

Release process of Vdsm version `VERSION` consists of the following
steps:

- Changing `Version:` field value in `vdsm.spec.in` to `VERSION`.

- Updating `%changelog` line in `vdsm.spec.in` to the current date,
  the committer, and `VERSION`.

- Committing these changes, with subject "New release: `VERSION`" and
  posting the patch to GitHub.

- Verifying the patch by checking that the GitHub CI build produced a
  correct set of rpm's with the correct version.

- Merging the patch (no review needed).

- Tagging the commit immediately after merge with an annotated tag:
  `git tag -a vVERSION`

- Making a new release in the GitHub repo.


## CI

Running tests locally is convenient, but before your changes can be
merged, we need to test them on all supported distributions and
architectures.

When you push patches to GitHub, CI will run its tests according to the
configuration in the `.github/workflows/ci.yml` file.


## Advanced Configuration

Before running `make` you could use `./configure` to set some (rarely used) options.
To see the list of options: `./configure -h`.


## SPDX headers

All files must include the SPDX copyright notice and the license identifier.
This project employs [reuse](https://reuse.software/) to handle copyright
notices and ensure that all files have the proper SPDX headers.
To add the SPDX headers to new files in the project you can use:

    contrib/add-spdx-header.sh new_file.py

This will create default `GPL-2.0-or-later` license header
with `Red Hat, Inc.` as copyright holder.

```
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later
```
To add new license to be used in the project:

    reuse download <License-Identifier>

Check list of available license identifier in https://spdx.org/licenses/.

To add SPDX header to a file with a non-default license:

    reuse addheader
      --copyright="Red Hat, Inc." \
      --license="<License-Identifier>" \
      --template=vdsm.jinja2 \
      --exclude-year \
      new_file.py

Please check that all files are reuse-compliant before pushing your branch:

    make reuse


## Coding guidelines

The coding conventions in Vdsm are generally aligned with the
[PEP 8](https://peps.python.org/pep-0008/) style recommendations.
Code is linted with `flake8` and `pylint` based in the `/.pylintrc` file.
You can trigger the linters check by pushing changes into your fork, which invokes:

    make lint

However, some code conventions are not explicitly enforced by the linters due
to their limitations. Nonetheless, new code shall adhere to those
rules, and old code should be updated as possible when patched.

In general, it is very important for new code to be consistent with the existing
code in the same module, or in the same package for new modules. In case of
doubt, check the already existing code before!

In this section, we will cover some of the most important conventions and
preferences for contributing to Vdsm, as extension to PEP 8 general rules.

### Imports

First import standard library modules, then 3rd party modules, and finally
project modules.
Separate standard library modules and 3rd party modules with a blank line.
Keep alphabetical order within each block.

Less specific imports:

    from vdsm.foo import baz

Should come before more specific imports like:

    from vdsm.foo.bar import baz

Relative name imports are accepted, but better to avoid.

### Indentation

Use 4 spaces per indentation level.

Continuation lines should align wrapped elements either vertically using
Python's implicit line joining inside parentheses, brackets and braces, or
using a hanging indent. When using a hanging indent, the following should
be considered:
- There should be no arguments on the first line.
- Further indentation should be used to clearly distinguish itself as a continuation line.
- Hanging indentation should stick to the 4-space rule.
```python
# Add 4 spaces (an extra level of indentation) to distinguish arguments from the rest.
# Consider that having too many arguments in a function is an anti-pattern.
foo = long_function_name(
    var_one, var_two, var_three, var_four)

# Recommended extra indentation with multiline if-statements.
if (condition_1 and condition_2 [...]
        and condition_n):
    do_something()

# But this is also possible.
def long_function_name(
        var_one, var_two, var_three,
        var_four):
    print(var_one)
```

To break comprehension Lists, Dicts, Sets as well as generator expressions,
keep expressions that would be written in a single line together.
Continuation lines stick to the same rules as above. Closing
brace/bracket/parenthesis, if placed on a separate line, shall line up
under the first character of the line start.

```python
# Vertically aligned and extra 4 spaces for condition continuation
foo = [var for var in bar_list
       if condition_1
           and condition_2
           and condition_3]
```

### Docstrings

Always use the three double-quote `"""` format for docstrings
(per [PEP 257](https://peps.python.org/pep-0257/)).
A docstring shall be organized as multiple summary lines (one physical line
not exceeding 79 characters) terminated by a period. First and last line
of the docstring shall be blank.

Module functions and classes should have a docstring, unless:
- Not public.
- Pertain to a test module.

Class methods do not require docstring, but is recommended when it is a public
entity or have a complex interface.

The docstring should be imperative-style (e.g., `Verify configuration file.`)
rather than descriptive-style (e.g., `Verifies configuration file.`).
The docstring should describe the function's main purpose, its calling syntax,
and/or semantics, but avoid implementation details.

Certain aspects of a function shall be documented in special sections,
listed below.

#### Args: (or Arguments:)

List each parameter by name. It is strongly encouraged that the type of the
parameter follows the name, either enclosed in parentheses, with clear wording,
or in [type annotation](https://docs.python.org/3/library/typing.html) format.
Then, the description of the parameter separated by a colon. If the type is
optional or can take multiple types, that can be specified with
comma-separated lists or use type annotation syntax.
If the description of the parameter is too long to fit in 79 characters,
following hanging lines can be indented with 2 or 4 extra spaces
(as long as it is consistent with surrounding docstrings).

#### Returns: (or Yields: for generators)

Start with the type of the return value, followed by a colon and its semantics.
If the function only returns None, this section is not required. It may also
be omitted if the docstring starts with Return or Yield
(e.g., `Return a boolean indicating if the folder has the right permissions.`),
and the opening sentence is sufficient to describe the return value.

#### Raises:

List all exceptions that are relevant to the interface, followed by a
description. Use a similar exception name + (type) + colon + space and hanging
indent style as described in `Args:`.

```python
def example_function(arg_1, arg_2=None):
    """
    This is an example docstring. Start with summary explanation of the
    function main purpose. Can expand multiple lines.

    Args:
        arg_1 (_type_): Long description can be split into
            multiple lines with extra indentation.
        arg_2 (_type_): _description_

    Returns:
        _type_: _description_

    Raises:
        OSError: _description_
    """
```

### Comment blocks and inline comments

Comments are accepted in non-obvious parts of the code that need extra explanation.
A comment can expand through multiple lines before the operation starts.
Comments should have normal punctuation and grammar. Start sentences with
uppercase letters, end with periods, and use commas where necessary.
Comments should never describe the code.

Inline comments are allowed, but discouraged. Inline comments are acceptable
for very short comments, but shall never expand through multiple lines.
To improve legibility, multiple inline comments in successive lines should be
vertically aligned.

```python
    _MULTIPATHD = cmdutils.CommandPath(
        "multipathd",
        "/usr/sbin/multipathd",  # Fedora, EL7
        "/sbin/multipathd")      # Ubuntu

    # List of multipath devices that should never be handled by Vdsm. The
    # main use case is to filter out the multipath devices the host is
    # booting from when configuring hypervisor to boot from SAN. This device
    # must have a special rule to queue I/O when all paths have failed, and
    # accessing it in Vdsm commands may hang Vdsm.

```

### Git commits

We cultivate a discipline of clean, linear git history that dates back to
[gerrit](https://gerrit.ovirt.org/q/project:vdsm+is:merged) days. Commit messages
matter - we have a
[template](https://github.com/oVirt/vdsm/blob/master/commit-template.txt) for
them. Every commit should be atomic and releasable. We don't use merge commits -
we use rebasing.
