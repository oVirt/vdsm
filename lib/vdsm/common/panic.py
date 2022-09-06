# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import os
import sys
import threading


def panic(msg):
    logging.exception("Panic: %s", msg)
    ready = threading.Event()

    def run():
        ready.wait(10)
        os.killpg(0, 9)
        sys.exit(-3)

    t = threading.Thread(target=run)
    t.daemon = True
    t.start()
    try:
        logging.shutdown()
    finally:
        ready.set()
