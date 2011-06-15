import os
import threading
import logging
from multiprocessing.managers import BaseManager

ADDRESS = "/var/run/vdsm/debugplugin.sock"
log = logging.getLogger("DebugInterpreter")

class DebugInterpreterManager(BaseManager): pass

class DebugInterpreter(object):
    def execute(self, code):
        exec(code)

def __turnOnDebugPlugin():
    log.warn("Starting Debug Interpreter. Tread lightly!")
    try:
        if os.path.exists(ADDRESS):
            os.unlink(ADDRESS)
        manager = DebugInterpreterManager(address=ADDRESS, authkey="KEY")
        interpreter = DebugInterpreter()
        manager.register('interpreter', callable=lambda:interpreter)
        server = manager.get_server()
        servThread = threading.Thread(target=server.serve_forever)
        servThread.setDaemon(True)
        servThread.start()
    except:
        log.error("Could not start debug plugin", exc_info=True)

__turnOnDebugPlugin()

