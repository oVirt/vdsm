from threading import Lock
import misc
from vdsm import constants

# iscsiadm exit statuses
ISCSI_ERR_SESS_EXISTS = 15
ISCSI_ERR_LOGIN_AUTH_FAILED = 24
ISCSI_ERR_OBJECT_NOT_FOUND = 21


class IscsiError(RuntimeError):
    pass


class ReservedInterfaceNameError(IscsiError):
    pass


class IscsiInterfaceError(IscsiError):
    pass


class IsciInterfaceAlreadyExistsError(IscsiInterfaceError):
    pass


class IsciInterfaceCreationError(IscsiInterfaceError):
    pass


class IscsiInterfaceDoesNotExistError(IscsiInterfaceError):
    pass


class IscsiInterfaceUpdateError(IscsiInterfaceError):
    pass


class IscsiInterfaceDeletionError(IscsiInterfaceError):
    pass


class IscsiDiscoverdbError(IscsiError):
    pass


class IscsiInterfaceListingError(IscsiError):
    pass


class IscsiAuthenticationError(IscsiError):
    pass


class IscsiNodeError(IscsiError):
    pass


class IscsiSessionNotFound(IscsiError):
    pass


class IscsiSessionError(IscsiError):
    pass

_RESERVED_INTERFACES = ("default", "tcp", "iser")

# Running multiple iscsiadm commands in parallel causes random problems.
# This serializes all calls to iscsiadm.
# Remove when iscsid is actually thread safe.
_iscsiadmLock = Lock()


def _runCmd(args, hideValue=False):
    # FIXME: I don't use supervdsm because this entire module has to just be
    # run as root and there is no such feature yet in supervdsm. When such
    # feature exists please change this.
    with _iscsiadmLock:
        cmd = [constants.EXT_ISCSIADM] + args

        printCmd = None
        if hideValue:
            printCmd = cmd[:]
            for i, arg in enumerate(printCmd):
                if arg != "-v":
                    continue

                if i < (len(printCmd) - 1):
                    printCmd[i + 1] = "****"

        return misc.execCmd(cmd, printable=printCmd, sudo=True)


def iface_exists(interfaceName):
    #FIXME: can be optimized by checking /var/lib/iscsi/ifaces
    return interfaceName in iface_list()


def iface_new(name):
    if name in _RESERVED_INTERFACES:
        raise ReservedInterfaceNameError(name)

    rc, out, err = _runCmd(["-m", "iface", "-I", name, "--op=new"])
    if rc == 0:
        return

    if iface_exists(name):
        raise IsciInterfaceAlreadyExistsError(name)

    raise IsciInterfaceCreationError(name, rc, out, err)


def iface_update(name, key, value):
    rc, out, err = _runCmd(["-m", "iface", "-I", name, "-n", key, "-v", value,
                            "--op=update"])
    if rc == 0:
        return

    if not iface_exists(name):
        raise IscsiInterfaceDoesNotExistError(name)

    raise IscsiInterfaceUpdateError(name, rc, out, err)


def iface_delete(name):
    rc, out, err = _runCmd(["-m", "iface", "-I", name, "--op=delete"])
    if rc == 0:
        return

    if not iface_exists(name):
        raise IscsiInterfaceDoesNotExistError(name)

    raise IscsiInterfaceDeletionError(name)


def iface_list():
    # FIXME: This can be done more efficiently by iterating
    # /var/lib/iscsi/ifaces. Fix if ever a performance bottleneck.
    rc, out, err = _runCmd(["-m", "iface"])
    if rc == 0:
        return [line.split()[0] for line in out]

    raise IscsiInterfaceListingError(rc, out, err)


def iface_info(name):
    # FIXME: This can be done more effciently by reading
    # /var/lib/iscsi/ifaces/<iface name>. Fix if ever a performance bottleneck.
    rc, out, err = _runCmd(["-m", "iface", "-I", name])
    if rc == 0:
        res = {}
        for line in out:
            if line.startswith("#"):
                continue

            key, value = line.split("=", 1)
            res[key.strip()] = value.strip()

        return res

    if not iface_exists(name):
        raise IscsiInterfaceDoesNotExistError(name)

    raise IscsiInterfaceListingError(rc, out, err)


def discoverydb_new(discoveryType, iface, portal):
    rc, out, err = _runCmd(["-m", "discoverydb", "-t", discoveryType, "-I",
                            iface, "-p", portal, "--op=new"])
    if rc == 0:
        return

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    raise IscsiDiscoverdbError(rc, out, err)


def discoverydb_update(discoveryType, iface, portal, key, value,
                       hideValue=False):
    rc, out, err = _runCmd(["-m", "discoverydb", "-t", discoveryType, "-I",
                            iface, "-p", portal, "-n", key, "-v", value,
                            "--op=update"],
                           hideValue)
    if rc == 0:
        return

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    raise IscsiDiscoverdbError(rc, out, err)


def discoverydb_discover(discoveryType, iface, portal):
    rc, out, err = _runCmd(["-m", "discoverydb", "-t", discoveryType, "-I",
                            iface, "-p", portal, "--discover"])
    if rc == 0:
        res = []
        for line in out:
            if line.startswith("["):  # skip IPv6 targets
                continue
            rest, iqn = line.split()
            rest, tpgt = rest.split(",")
            ip, port = rest.split(":")
            res.append((ip, int(port), int(tpgt), iqn))

        return res

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    if rc == ISCSI_ERR_LOGIN_AUTH_FAILED:
        raise IscsiAuthenticationError(rc, out, err)

    raise IscsiDiscoverdbError(rc, out, err)


def discoverydb_delete(discoveryType, iface, portal):
    rc, out, err = _runCmd(["-m", "discoverydb", "-t", discoveryType, "-I",
                            iface, "-p", portal, "--op=delete"])
    if rc == 0:
        return

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    raise IscsiDiscoverdbError(rc, out, err)


def node_new(iface, portal, targetName):
    rc, out, err = _runCmd(["-m", "node", "-T", targetName, "-I", iface, "-p",
                            portal, "--op=new"])
    if rc == 0:
        return

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    raise IscsiNodeError(rc, out, err)


def node_update(iface, portal, targetName, key, value, hideValue=False):
    rc, out, err = _runCmd(["-m", "node", "-T", targetName, "-I", iface, "-p",
                            portal, "-n", key, "-v", value, "--op=update"],
                           hideValue)
    if rc == 0:
        return

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    raise IscsiNodeError(rc, out, err)


def node_delete(iface, portal, targetName):
    rc, out, err = _runCmd(["-m", "node", "-T", targetName, "-I", iface, "-p",
                            portal, "--op=delete"])
    if rc == 0:
        return

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    raise IscsiNodeError(rc, out, err)


def node_disconnect(iface, portal, targetName):
    rc, out, err = _runCmd(["-m", "node", "-T", targetName, "-I", iface, "-p",
                            portal, "-u"])
    if rc == 0:
        return

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    if rc == ISCSI_ERR_OBJECT_NOT_FOUND:
        raise IscsiSessionNotFound(iface, portal, targetName)

    raise IscsiNodeError(rc, out, err)


def node_login(iface, portal, targetName):
    rc, out, err = _runCmd(["-m", "node", "-T", targetName, "-I", iface, "-p",
                            portal, "-l"])
    if rc == 0:
        return

    if not iface_exists(iface):
        raise IscsiInterfaceDoesNotExistError(iface)

    if rc == ISCSI_ERR_LOGIN_AUTH_FAILED:
        raise IscsiAuthenticationError(rc, out, err)

    raise IscsiNodeError(rc, out, err)


def session_rescan():
    rc, out, err = _runCmd(["-m", "session", "-R"])
    if rc == 0:
        return

    raise IscsiSessionError(rc, out, err)


def session_logout(sessionId):
    rc, out, err = _runCmd(["-m", "session", "-r", str(sessionId), "-u"])
    if rc == 0:
        return

    raise IscsiSessionError(rc, out, err)
