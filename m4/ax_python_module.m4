# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-FileCopyrightText: 2008 Andrew Collier
# SPDX-License-Identifier: GPL-2.0-or-later
#
# ===========================================================================
#     http://www.gnu.org/software/autoconf-archive/ax_python_module.html
# ===========================================================================
#
# SYNOPSIS
#
#   AX_PYTHON_MODULE(modname[, fatal, python])
#
# DESCRIPTION
#
#   Checks for Python module.
#
#   If fatal is non-empty then absence of a module will trigger an error.
#   The third parameter can either be "python" for Python 2 or "python3" for
#   Python 3; defaults to Python 3.

#serial 8

AU_ALIAS([AC_PYTHON_MODULE], [AX_PYTHON_MODULE])
AC_DEFUN([AX_PYTHON_MODULE],[
    if test -z "$3";
    then
        THIS_MODULE_PYTHON="python3"
    else
        THIS_MODULE_PYTHON="$3"
    fi

    PYTHON_NAME=`basename $THIS_MODULE_PYTHON`
    AC_MSG_CHECKING($PYTHON_NAME module: $1)
    "$THIS_MODULE_PYTHON" -c "import $1" 2>/dev/null
    if test $? -eq 0;
    then
        AC_MSG_RESULT(yes)
        eval AS_TR_CPP(HAVE_PYMOD_$1)=yes
    else
        AC_MSG_RESULT(no)
        eval AS_TR_CPP(HAVE_PYMOD_$1)=no
        #
        if test -n "$2"
        then
            AC_MSG_ERROR(failed to find required module $1)
            exit 1
        fi
    fi
])
