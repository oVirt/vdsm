"""
Release the SPM lease behind vdsm back.
"""
import subprocess
import sys

import sanlock

print("Looking up vdsm pid")
out = subprocess.check_output(["pgrep", "^vdsmd"]).decode("utf-8")
vdsm_pid = int(out)

print("Inquiring process {} resoruces".format(vdsm_pid))
for r in sanlock.inquire(pid=vdsm_pid):
    if r["resource"] == b"SDM":
        break
else:
    print("Not SPM host", file=sys.stderr)
    sys.exit(1)

print("Releasing SPM lease {}".format(r))
sanlock.release(r["lockspace"], r["resource"], r["disks"], pid=vdsm_pid)
