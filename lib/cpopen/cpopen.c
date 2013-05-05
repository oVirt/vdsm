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
#include <sys/prctl.h>

static PyObject *createProcess(PyObject *self, PyObject *args);
static PyMethodDef CreateProcessMethods[];
static void closeFDs(int errnofd);

/* Python boilerplate */
static PyMethodDef
CreateProcessMethods[] = {
    {"createProcess",  createProcess, METH_VARARGS,
     "Execute a command."},
    {NULL, NULL, 0, NULL}        /* Sentinel */
};

PyMODINIT_FUNC
initcpopen(void)
{
    PyObject *m;

    m = Py_InitModule("cpopen", CreateProcessMethods);

    /* In the future put other init code after this condition. */
    if (m == NULL)
        return;
}

/* Just like close() but retries on interrupt */
static int
safeClose(int fd) {
    int rv;

retry:
    rv = close(fd);
    if ((rv < 0) && (errno == EINTR)) {
        goto retry;
    }

    return rv;
}

static int
setCloseOnExec(int fd) {
    int flags;

    flags = fcntl(fd, F_GETFD);
    if (flags == -1) {
      return -1;
    }

    if (fcntl(fd, F_SETFD, flags | FD_CLOEXEC) == -1) {
      return -1;
    }

    return 0;
}

/* Closes all open FDs except for stdin, stdout and stderr */
static void
closeFDs(int errnofd) {
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

        if (fdNum == errnofd) {
            continue;
        }

        safeClose(fdNum);
    }

    closedir(dp);
    safeClose(dfd);
}

static void
freeStringArray(char** arr) {
    char** item;
    for (item = arr; *item != NULL; item++) {
        PyMem_Free(*item);
    }

    free(arr);
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
        if (!PyArg_Parse(PyList_GetItem(list, i),
                         "et;",
                         Py_FileSystemDefaultEncoding,
                         &argv[i])) {
            PyErr_SetString(PyExc_TypeError,
                            "createProcess() arg 2 must contain only strings");
            goto fail;
        }
    }

    return argv;

fail:
    freeStringArray(argv);
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
    int deathSignal = 0;
    int rv;

    int outfd[2] = {-1, -1};
    int in1fd[2] = {-1, -1};
    int in2fd[2] = {-1, -1};

    int errnofd[2] = {-1, -1};
    int childErrno = 0;

    PyObject* pyArgList;
    PyObject* pyEnvList;
    const char* cwd;
    int close_fds = 0;

    char** argv = NULL;
    char** envp = NULL;

    if (!PyArg_ParseTuple(args, "O!iiiiiiizOi:createProcess;",
                &PyList_Type, &pyArgList, &close_fds,
                &outfd[0], &outfd[1],
                &in1fd[0], &in1fd[1],
                &in2fd[0], &in2fd[1],
                &cwd, &pyEnvList, &deathSignal)) {
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

    if(pipe(errnofd) < 0) {
        PyErr_SetFromErrno(PyExc_OSError);
        goto fail;
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
        safeClose(0);
        safeClose(1);
        safeClose(2);

        dup2(outfd[0], 0);
        dup2(in1fd[1], 1);
        dup2(in2fd[1], 2);

        safeClose(outfd[0]);
        safeClose(outfd[1]);
        safeClose(in1fd[0]);
        safeClose(in1fd[1]);
        safeClose(in2fd[0]);
        safeClose(in2fd[1]);
        safeClose(errnofd[0]);

        if (deathSignal) {
            childErrno = prctl(PR_SET_PDEATHSIG, deathSignal);
            if (childErrno < 0) {
                childErrno = errno;
            }
            /* Check that parent did not already die between fork and us
             * setting the death signal */
            if (write(errnofd[1], &childErrno, sizeof(int)) < sizeof(int)) {
                exit(-1);
            }

            if (childErrno != 0) {
                exit(-1);
            }
        }

        if (setCloseOnExec(errnofd[1]) < 0) {
            goto sendErrno;
        }

        if (close_fds) {
            closeFDs(errnofd[1]);
        }

        if (cwd) {
            if (chdir(cwd) < 0) {
                goto sendErrno;
            }
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
sendErrno:
        if (write(errnofd[1], &errno, sizeof(int)) < 0) {
            exit(errno);
        }
        exit(-1);
    }

    safeClose(errnofd[1]);
    errnofd[1] = -1;

    if (deathSignal) {
        /* death signal sync point */
        while (1) {
            rv = read(errnofd[0], &childErrno, sizeof(int));
            if (rv < 0) {
                switch (errno) {
                    case EINTR:
                    case EAGAIN:
                        break;
                    default:
                        PyErr_SetString(PyExc_OSError, strerror(childErrno));
                        goto fail;

                }
            } else if (rv < sizeof(int)) {
                PyErr_SetString(PyExc_OSError, strerror(childErrno));
                goto fail;
            }
            break;
        }

        if (childErrno != 0) {
            PyErr_SetString(PyExc_OSError, strerror(childErrno));
            goto fail;
        }
    }

    /* error sync point */
    if (read(errnofd[0], &childErrno, sizeof(int)) == sizeof(int)) {
        PyErr_SetString(PyExc_OSError, strerror(childErrno));
        goto fail;
    }

    safeClose(errnofd[0]);
    errnofd[0] = -1;

    /* From this point errors shouldn't occur, if they do something is very
     * very very wrong */

    freeStringArray(argv);

    if (envp) {
        freeStringArray(envp);
    }

    return Py_BuildValue("(iiii)", cpid, outfd[1], in1fd[0], in2fd[0]);

fail:
    if (argv) {
        freeStringArray(argv);
    }

    if (envp) {
        freeStringArray(envp);
    }

    if (errnofd[0] >= 0) {
        safeClose(errnofd[0]);
    }

    if (errnofd[1] >= 0) {
        safeClose(errnofd[1]);
    }

    return NULL;
}
