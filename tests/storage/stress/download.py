"""
Stress tests for vdsm http server.

Usage:

1. Create a 1 GiB raw disk
2. Update HOST, POOL_ID, DOMAIN_ID, IMAGE_ID with the details of your setup.
3. Run this on the SPM host:

    python3 download.py

If there were errors, the script will log them, exit with zeon-zero exit code,
and print the number of errors per reader thread.

See https://bugzilla.redhat.com/1694972
"""

import json
import logging
import ssl
import sys
import threading
import time

from collections import defaultdict
from contextlib import closing
from http import client as http_client

from vdsm import client as vdsm_client

log = logging.getLogger()

# You need to adpat these to your setup.
HOST = "host4"
PORT = 54321
POOL_ID = "86b5a5ca-5376-4cef-a8f7-d1dc1ee144b4"
DOMAIN_ID = "0ce9ba3e-68c0-41fa-888e-75ca36ca6452"
IMAGE_ID = "7f6e0f6d-1c82-43f8-9641-3fa72e5de42a"

headers = {
    "Storage-Pool-Id": POOL_ID,
    "Storage-Domain-Id": DOMAIN_ID,
    "Image-Id": IMAGE_ID,
    "Range": "bytes=0-20479",
}

context = ssl.create_default_context(cafile="/etc/pki/vdsm/certs/cacert.pem")

context.load_cert_chain(
    certfile="/etc/pki/vdsm/certs/vdsmcert.pem",
    keyfile="/etc/pki/vdsm/keys/vdsmkey.pem")


def reader(ctx, headers, errors):
    log.info("Reader started")

    name = threading.current_thread().name

    # We need vdsm client to clear tasks after downloads and report errors
    # since error handling in the http server is poor.
    vdsm = vdsm_client.connect("localhost", 54321)

    # We need http client to download image data.
    http = http_client.HTTPSConnection(HOST, PORT, context=ctx, timeout=10)

    with closing(vdsm), closing(http):
        for i in range(100):
            log.debug("Sending request %s", i)
            http.request("GET", "/", headers=headers)

            task_id = None
            r = http.getresponse()
            try:
                log.debug(
                    "Received response %s: status=%r reason=%r headers=%r",
                    i, r.status, r.reason, r.getheaders())

                task_id = r.getheader("Task-Id")

                # Did we fail to start the download?
                if r.status != http_client.PARTIAL_CONTENT:
                    error = r.read()[:200].decode("utf-8", errors="replace")
                    raise RuntimeError("Request failed: {}".format(error))

                # This may fail if the internal task failed, and we will just
                # time out waiting for the data.
                try:
                    r.read()
                except (http_client.HTTPException, OSError) as e:
                    log.error(
                        "Reading payload failed: %s, closing connection", e)
                    http.close()
            finally:
                if task_id:
                    log.debug("Waiting until task %s is finished", task_id)

                    # {'taskID': '5e7b6cd0-d9d7-4e48-b525-7f1f0a612ff7',
                    # 'taskState': 'running', 'taskResult': '', 'code': 0,
                    # 'message': 'running job 1 of 1'}
                    while True:
                        time.sleep(0.5)
                        status = vdsm.Task.getStatus(taskID=task_id)
                        log.debug("Task status: %s", status)
                        if status["taskState"] != "running":
                            break

                    if status["code"] != 0:
                        log.error("Task failed: %s", status["message"])
                        errors[name] += 1

                    log.debug("Clearing task %s", task_id)
                    vdsm.Task.clear(taskID=task_id)

    log.info("Reader finished")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s (%(threadName)s) %(message)s")

log.info("Starting readers")

errors = defaultdict(int)
readers = []

for i in range(10):
    name = "reader/{}".format(i)
    log.debug("Starting reader %s", name)

    t = threading.Thread(
        target=reader,
        args=(context, headers, errors),
        daemon=True,
        name=name)

    t.start()
    readers.append(t)

for t in readers:
    t.join()

log.info("Readers finished")

if errors:
    print(json.dumps(errors, indent=4))
    sys.exit(1)
