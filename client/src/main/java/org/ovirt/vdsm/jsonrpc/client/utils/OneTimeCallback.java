package org.ovirt.vdsm.jsonrpc.client.utils;

import java.util.concurrent.atomic.AtomicBoolean;

public abstract class OneTimeCallback {

    private AtomicBoolean executed = new AtomicBoolean(false);

    public void checkAndExecute() {
        if (this.executed.compareAndSet(false, true)) {
            execute();
        }
    }

    public abstract void execute();
}
