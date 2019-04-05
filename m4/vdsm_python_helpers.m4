#
# Copyright 2019 Red Hat, Inc.
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

#
# VDSM_CHECK_PY_VERSIONS(TARGET_PY_VERSION, CHECKED_PY_VERSIONS)
# --------------------------------------------------------------
# Sets up variables that are helpful when working with multiple
# Python interpreter versions. Calls 'AM_PATH_PYTHON' with 'PYTHON'
# variable defined as 'TARGET_PY_VERSION'.
#
# 'TARGET_PY_VERSION' argument defines a Python version that must
# be available in the system and will be used as the main version
# (for packaging, etc.). It needs to be defined in a 'pythonMAJOR.MINOR'
# or  'pythonMAJOR' form (i.e. 'python3.6', 'python3'). If 'pythonMAJOR'
# form is used, an attempt to resolve to 'pythonMAJOR.MINOR' form will
# be made by following symlinks. If 'TARGET_PY_VERSION' is not available,
# an error will be raised.
#
# 'CHECKED_PY_VERSIONS' is a space-separated string containing interpreter
# names in a 'pythonMAJOR.MINOR' or 'pythonMAJOR' form, that will be checked
# for availability (i.e. 'python2.7 python3.6 python3.7' or simply
# 'python2 python3'). If 'pythonMAJOR' form is used, an attempt to resolve
# to 'pythonMAJOR.MINOR' form will be made by following symlinks.
#
# Variables defined by the macro come handy for writing shell loops, i.e.:
#
#     for version in $VDSM_SUPPORTED_PY_MAJOR_VERSIONS; do
#         dnf install -y ${version}-six
#     done
#
# Defined variables are:
#
#   VDSM_SUPPORTED_PY_VERSIONS
#   --------------------------
#   A space-separated string consisting of all available interpreter versions
#   in a 'pythonMAJOR.MINOR' form (i.e. 'python2.7 python3.6 python3.7').
#
#   VDSM_SUPPORTED_PY_MAJOR_VERSIONS
#   --------------------------------
#   A space-separated string consisting of all available interpreters
#   in a 'pythonMAJOR' form (i.e. 'python2 python3'). If there is more
#   than one MINOR version of 'pythonMAJOR' interpreter available
#   (i.e. we have both 'python3.6' and 'python3.7'), 'pythonMAJOR'
#   version will be listed only once ('python3').
#
#   VDSM_SUPPORTED_PY_MAJOR_SHORT_VERSIONS
#   --------------------------------------
#   Analogous to 'VDSM_SUPPORTED_PY_MAJOR_VERSIONS', but uses 'pyMAJOR'
#   form (i.e. 'py2').
#
#   VDSM_SUPPORTED_PY_SHORT_VERSIONS
#   --------------------------------
#   A space-separated string consisting of all available interpreters
#   in a 'pyMAJORMINOR' form (i.e. 'py27 py36 py37').
#
#   VDSM_TARGET_PY_VERSION
#   ----------------------
#   Target Python interpreter version in a 'pythonMAJOR.MINOR'
#   form (i.e. 'python3.6').
#
#   VDSM_TARGET_PY_MAJOR_VERSION
#   ----------------------------
#   Target Python interpreter version in a 'pythonMAJOR'
#   form (i.e. 'python3').
#
#   VDSM_TARGET_PY_MAJOR_SHORT_VERSION
#   ----------------------------------
#   Target Python interpreter version in a 'pyMAJOR'
#   form (i.e. 'py3').
#
#   VDSM_TARGET_PY_SHORT_VERSION
#   ----------------------------
#   Target Python interpreter version in a 'pyMAJORMINOR'
#   form (i.e. 'py36').
#
AC_DEFUN([VDSM_CHECK_PY_VERSIONS], [
    _target_py_name="$1"
    _checked_py_versions="$2"
    _vdsm_resolve_ambiguous_interpreter_name(_target_py_name)

    for _checked_py_version in ${_checked_py_versions}; do
        _vdsm_resolve_ambiguous_interpreter_name(_checked_py_version)
        AM_PYTHON_CHECK_VERSION([${_checked_py_version}], _vdsm_py_version_number([${_checked_py_version}]), [
            _vdsm_append_string_to_var(_available_py_versions, [${_checked_py_version}])
            _vdsm_append_string_to_var(_available_py_major_versions, _vdsm_py_major_name([${_checked_py_version}]))
            _vdsm_append_string_to_var(_available_py_major_short_versions, _vdsm_py_major_short_name([${_checked_py_version}]))
            _vdsm_append_string_to_var(_available_py_short_versions, _vdsm_py_short_name([${_checked_py_version}]))
        ])
    done

    AS_IF([echo "${_available_py_versions}" | grep -v ${_target_py_name}], [
        AC_MSG_ERROR([Desired TARGET_PY_VERSION=${_target_py_name} is not available])])

    AC_SUBST([VDSM_SUPPORTED_PY_VERSIONS], [${_available_py_versions}])
    AC_SUBST([VDSM_SUPPORTED_PY_MAJOR_VERSIONS], [${_available_py_major_versions}])
    AC_SUBST([VDSM_SUPPORTED_PY_MAJOR_SHORT_VERSIONS], [${_available_py_major_short_versions}])
    AC_SUBST([VDSM_SUPPORTED_PY_SHORT_VERSIONS], [${_available_py_short_versions}])

    AC_SUBST([VDSM_TARGET_PY_VERSION], [${_target_py_name}])
    AC_SUBST([VDSM_TARGET_PY_MAJOR_VERSION], [_vdsm_py_major_name(${_target_py_name})])
    AC_SUBST([VDSM_TARGET_PY_MAJOR_SHORT_VERSION], [_vdsm_py_major_short_name(${_target_py_name})])
    AC_SUBST([VDSM_TARGET_PY_SHORT_VERSION], [_vdsm_py_short_name(${_target_py_name})])

    PYTHON=${_target_py_name}
    AM_PATH_PYTHON
])

#
# VDSM_DISABLE_PY3_VERSIONS
# -------------------------
# Runs 'VDSM_DISABLE_PY_VERSION' on all found version 3 interpreters.
#
AC_DEFUN([VDSM_DISABLE_PY3_VERSIONS], [_vdsm_disable_pyx_versions(3)])

#
# VDSM_DISABLE_PY2_VERSIONS
# -------------------------
# Runs 'VDSM_DISABLE_PY_VERSION' on all found version 2 interpreters.
#
AC_DEFUN([VDSM_DISABLE_PY2_VERSIONS], [_vdsm_disable_pyx_versions(2)])

#
# VDSM_DISABLE_PY_VERSION(PY_VERSION)
# -----------------------------------
# Removes 'PY_VERSION' from variables defining Python interpreter
# availability. 'PY_VERSION' must have a 'pythonMAJOR.MINOR' form.
# Raises an error if the user tries to disable 'VDSM_TARGET_PY_VERSION'
# version. Calling this macro makes sense only after a call
# to 'VDSM_CHECK_PY_VERSIONS'.
#
AC_DEFUN([VDSM_DISABLE_PY_VERSION], [
    _vdsm_validate_interpreter_name($1)

    if test ${VDSM_TARGET_PY_VERSION} = $1; then
        AC_MSG_ERROR([Cannot disable target Python version: $1])
    fi

    _vdsm_remove_string_from_var(VDSM_SUPPORTED_PY_VERSIONS, $1)
    _vdsm_remove_string_from_var(VDSM_SUPPORTED_PY_SHORT_VERSIONS, _vdsm_py_short_name($1))

    _disabled_py_major_name=_vdsm_py_major_name($1)
    _remove_major_names="yes"

    for version in ${VDSM_SUPPORTED_PY_VERSIONS}; do
        if test _vdsm_py_major_name(${version}) = ${_disabled_py_major_name}; then
            _remove_major_names="no"
            break
        fi
    done

    if test ${_remove_major_names} = "yes"; then
        _vdsm_remove_string_from_var(VDSM_SUPPORTED_PY_MAJOR_VERSIONS, ${_disabled_py_major_name})
        _vdsm_remove_string_from_var(VDSM_SUPPORTED_PY_MAJOR_SHORT_VERSIONS, _vdsm_py_major_short_name($1))
    fi
])

#
# _vdsm_py_major_name
# -------------------
# Converts an interpreter name in a form pythonMAJOR.MINOR
# to a form pythonMAJOR (i.e. 'python3.6' -> 'python3')
#
AC_DEFUN([_vdsm_py_major_name], $([echo "$1" | sed -r 's/^(@<:@^\.@:>@+).*$/\1/']))

#
# _vdsm_py_major_short_name
# -------------------------
# Converts an interpreter name in a form pythonMAJOR.MINOR
# to a form pyMAJOR (i.e. 'python3.6' -> 'py3')
#
AC_DEFUN([_vdsm_py_major_short_name], $([echo "$1" | sed -r 's/^python(@<:@0-9@:>@+).*$/py\1/']))

#
# _vdsm_py_short_name
# -------------------
# Converts an interpreter name in a form pythonMAJOR.MINOR
# to a form pyMAJORMINOR (i.e. 'python3.6' -> 'py36')
#
AC_DEFUN([_vdsm_py_short_name], $([echo "$1" | sed -r 's/^python(@<:@0-9@:>@+)\.(@<:@0-9@:>@+)$/py\1\2/']))

#
# _vdsm_py_version_number
# -----------------------
# Extracts the version number from interpreter name
# in a form pythonMAJOR.MINOR (i.e. 'python3.6' -> '3.6')
#
AC_DEFUN([_vdsm_py_version_number], $([echo "$1" | sed -r 's/python//']))

#
# _vdsm_strip_string
# ------------------
# Removes space characters from the beginning and the end of a string,
# collapses multiple spaces into a single one.
#
AC_DEFUN([_vdsm_strip_string], $([echo "$1" | sed 's/ \+/ /g;s/^ //;s/ $//']))

#
# _vdsm_remove_duplicates
# -----------------------
# Removes repeated words in a string (i.e. 'a b a a c b' -> 'a b   c ')
#
AC_DEFUN([_vdsm_remove_duplicates], $([echo "$1" | awk '{for (i=1;i<=NF;i++) if (!a@<:@$i@:>@++) printf("%s%s",$i,FS)}']))

#
# _vdsm_remove_string
# -------------------
# Removes occurences of a word in a string (i.e. 'b a b c' - 'b' -> ' a  c').
# Warning! We're ignoring the fact, that the argument may contain dots, which
# will be treated as 'any character' by sed - works in our case.
#
AC_DEFUN([_vdsm_remove_string], $([echo "$1" | sed 's/'$2'//g']))

#
# _vdsm_append_string_to_var
# --------------------------
# Appends a string to a variable, ensuring that the variable
# doesn't contain duplicates (i.e. 'a b c' + 'a' -> 'a b c').
#
AC_DEFUN([_vdsm_append_string_to_var], [$1="_vdsm_remove_duplicates(_vdsm_strip_string($$1 $2))"])

#
# _vdsm_remove_string_from_var
# ----------------------------
# Removes a string from a variable (i.e. 'a b c' - 'b' -> 'a c')
#
AC_DEFUN([_vdsm_remove_string_from_var], [$1=_vdsm_strip_string(_vdsm_remove_string($$1, $2))])

#
# _vdsm_validate_interpreter_name
# -------------------------------
# Checks if the interpreter name matches 'pythonMAJOR.MINOR'
# form (i.e. 'python3.6'). If it doesn't, an error is raised.
#
AC_DEFUN([_vdsm_validate_interpreter_name], [
    _vdsm_is_valid_interpreter_name($1,
        ,
        AC_MSG_ERROR(m4_strip([Invalid interpreter name format: $1. \
                               The interpreter name should take 'pythonMAJOR.MINOR' form i.e. 'python2.7']))
    )
])

#
# _vdsm_is_valid_interpreter_name
# -------------------------------
# Checks if the interpreter name matches 'pythonMAJOR.MINOR'
# form (i.e. 'python3.6'). If it does, it evaluates to first argument,
# if it doesn't it evaluates to second argument.
#
AC_DEFUN([_vdsm_is_valid_interpreter_name], [
    AS_IF([echo $1 | grep -qe "^python@<:@1-9@:>@@<:@0-9@:>@*\.@<:@0-9@:>@\+$"], [$2], [$3])
])

#
# _vdsm_disable_pyx_versions(MAJOR_NO)
# ------------------------------------
# Runs 'VDSM_DISABLE_PY_VERSION' on all interpreter versions
# that match 'pythonMAJOR_NO'.
#
AC_DEFUN([_vdsm_disable_pyx_versions], [
    _available_py_versions="${VDSM_SUPPORTED_PY_VERSIONS}"

    for version in ${_available_py_versions}; do
        if (echo ${version} | grep -q "python$1"); then
            VDSM_DISABLE_PY_VERSION([${version}])
        fi
    done
])

#
# _vdsm_resolve_ambiguous_interpreter_name
# ----------------------------------------
# Resolves ambiguous names like 'pythonMAJOR' to 'pythonMAJOR.MINOR'
# format in the context of the current environment.
#
# The only argument is the name of the variable whose value will
# be modified to hold the resolved name. Its initial value should
# be the name we want to resolve (i.e. 'python3').
#
# The algorithm behind the resolution is symbolic link following until
# a satisfactory name is found, or the end of symbolic links chain reached.
# In the latter case, an error will be raised.
#
AC_DEFUN([_vdsm_resolve_ambiguous_interpreter_name], [
    _searched_py_name="$$1"

    _vdsm_is_valid_interpreter_name([${_searched_py_name}],,[
         if test "$$1" = "python2"; then
            $1=python2.7
         else
            # Since every variable here is global, we have to initialize
            # '_tested_py_path' so it's not contaminated by previous calls
            # to this function. Same thing applies to 'unset ac_cv_path__tested_py_path'.
            _tested_py_path=""

            AC_PATH_PROG([_tested_py_path], [${_searched_py_name}])
            unset ac_cv_path__tested_py_path

            if test -n "${_tested_py_path}"; then
                while true; do
                    _tested_py_basename="$(basename ${_tested_py_path})"
                    _vdsm_is_valid_interpreter_name([${_tested_py_basename}], [break])
                    _tested_py_path="$(readlink ${_tested_py_path})"

                    if test -z "${_tested_py_path}"; then
                        AC_MSG_ERROR([Could not determine full interpreter name for ${_searched_py_name}, please use 'pythonMAJOR.MINOR' form])
                    fi
                done

                $1="${_tested_py_basename}"
            fi
        fi
    ])
])
