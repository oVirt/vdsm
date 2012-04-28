/*
* Copyright 2012 Red Hat, Inc.
*
* This program is free software; you can redistribute it and/or modify
* it under the terms of the GNU General Public License as published by
* the Free Software Foundation; either version 2 of the License, or
* (at your option) any later version.
*
* This program is distributed in the hope that it will be useful,
* but WITHOUT ANY WARRANTY; without even the implied warranty of
* MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
* GNU General Public License for more details.
*
* You should have received a copy of the GNU General Public License
* along with this program; if not, write to the Free Software
* Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
*
* Refer to the README and COPYING files for full details of the license
*/

#include <Python.h>

#include <dirent.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>

static PyObject *createProcess(PyObject *self, PyObject *args);
static PyMethodDef CreateProcessMethods[];
static void closeFDs(void);

/* Python boilerplate */
static PyMethodDef
CreateProcessMethods[] = {
    {"createProcess",  createProcess, METH_VARARGS,
     "Execute a command."},
    {NULL, NULL, 0, NULL}        /* Sentinel */
};

PyMODINIT_FUNC
initcreateprocess(void)
{
    PyObject *m;

    m = Py_InitModule("createprocess", CreateProcessMethods);

    // In the future put other init code after this condition.
    if (m == NULL)
        return;
}

/* Closes all open FDs except for stdin, stdout and stderr */
static void
closeFDs(void) {
    DIR *dp;
    int dfd;
    struct dirent *ep;
    int fdNum = -1;

    dfd = open("/proc/self/fd/", O_RDONLY);
    dp = fdopendir(dfd);
    while ((ep = readdir(dp))) {
        if(sscanf(ep->d_name, "%d", &fdNum) < 1) {
            continue;
        }

        if (fdNum < 3) {
            continue;
        }

        if (fdNum == dfd) {
            continue;
        }

        close(fdNum);
    }

    closedir(dp);
    close(dfd);
}

/* Copies the strings from a python list to a null terminated array.
 * The strings are shallow copied and are owned by python.
 * Don't keep this array after the call.
 *
 * Returns a NULL terminated array of null strings. On error returns NULL and
 * sets the python error accordingly
 */
static char**
pyListToArray(PyObject* list, int checkIfEmpty) {
    PyObject *item;
    int argn;
    int i;
    char** argv;

    if (!PyList_Check(list)) {
        PyErr_SetString(PyExc_TypeError, "Argument must be a python list");
        return NULL;
    }

    argn = PyList_Size(list);
    if ((checkIfEmpty) && (argn < 1)) {
        PyErr_SetString(PyExc_ValueError, "List must not be empty");
        return NULL;
    }

    argv = calloc(argn + 1, sizeof(char*));
    if (!argv) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }

    for (i = 0; i < argn; i++) {
        item = PyList_GetItem(list, i);
        if (!PyString_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "All items in list must be strings");
            goto fail;
        }
        argv[i] = PyString_AsString(item);
    }

    return argv;

fail:
    free(argv);
    return NULL;
}

/* Python's implementation of Popen forks back to python before execing.
 * Forking a python proc is a very complex and volatile process.
 *
 * This is a simpler method of execing that doesn't go back to python after
 * forking. This allows for faster safer exec.
 *
 * return NULL on error and sets the python error accordingly.
 */
static PyObject *
createProcess(PyObject *self, PyObject *args)
{
    int cpid;

    int outfd[2] = {-1, -1};
    int in1fd[2] = {-1, -1};
    int in2fd[2] = {-1, -1};

    PyObject* pyArgList;
    PyObject* pyEnvList;
    const char* cwd;
    int close_fds = 0;

    char** argv = NULL;
    char** envp = NULL;

    if (!PyArg_ParseTuple(args, "O!iiiiiiizO:createProcess;",
                &PyList_Type, &pyArgList, &close_fds,
                &outfd[0], &outfd[1],
                &in1fd[0], &in1fd[1],
                &in2fd[0], &in2fd[1],
                &cwd, &pyEnvList)) {
        return NULL;
    }

    argv = pyListToArray(pyArgList, 1);
    if (!argv) {
        goto fail;
    }

    if (PyList_Check(pyEnvList)) {
        envp = pyListToArray(pyEnvList, 0);
        if (!envp) {
            goto fail;
        }
    }

try_fork:
    cpid = fork();
    if (cpid < 0) {
        if (errno == EAGAIN ||
            errno == EINTR ) {
            goto try_fork;
        }

        PyErr_SetFromErrno(PyExc_OSError);
        goto fail;
    }

    if (!cpid) {
        close(0);
        close(1);
        close(2);

        dup2(outfd[0], 0);
        dup2(in1fd[1], 1);
        dup2(in2fd[1], 2);

        close(outfd[0]);
        close(outfd[1]);
        close(in1fd[0]);
        close(in1fd[1]);
        close(in2fd[0]);
        close(in2fd[1]);

        if (close_fds) {
            closeFDs();
        }

        if (cwd) {
            /* this assignment is there to stop the compile warnings */
            cpid = chdir(cwd);
            setenv("PWD", cwd, 1);
        }
exec:
        if (envp) {
            execvpe(argv[0], argv, envp);
        } else {
            execvp(argv[0], argv);
        }

        if (errno == EINTR ||
            errno == EAGAIN )
        {
            goto exec;
        }
        fprintf(stderr, "exec failed: %s", strerror(errno));
        exit(errno);
    }

    /* From this point errors shouldn't occur, if they do something is very
     * very very wrong */

    free(argv);

    if (envp) {
        free(envp);
    }

    return Py_BuildValue("(iiii)", cpid, outfd[1], in1fd[0], in2fd[0]);

fail:
    if (argv) {
        free(argv);
    }

    if (envp) {
        free(envp);
    }

    return NULL;
}
