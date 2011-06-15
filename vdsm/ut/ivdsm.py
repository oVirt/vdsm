import os
import pwd
import logging
import threading
import sys

def runVdsm(baseDir="/usr/share/vdsm/", configFilePath="/etc/vdsm/vdsm.conf", loggerConfigurationPath='/etc/vdsm/logger.conf'):
    """
    Starts a VDSM instance in a new thread and returns a tuple ``(ClientIF, Thread Running VDSM)``
    """
    if pwd.getpwuid(os.geteuid())[0] != "vdsm":
        raise Exception("You can't run vdsm with any user other then 'vdsm'.")

    sys.path.append(baseDir)

    from config import config
    from logging import config as lconfig
    import clientIF

    loggerConfFile = loggerConfigurationPath
    lconfig.fileConfig(loggerConfFile)
    log = logging.getLogger('vds')

    config.read(configFilePath)

    cif = clientIF.clientIF(log)

    t = threading.Thread(target = cif.serve)
    t.setDaemon(True)
    t.start()

    return (cif, t)

