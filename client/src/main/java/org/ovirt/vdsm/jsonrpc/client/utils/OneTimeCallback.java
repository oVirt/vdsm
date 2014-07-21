package org.ovirt.vdsm.jsonrpc.client.utils;

import java.util.concurrent.atomic.AtomicBoolean;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;

public abstract class OneTimeCallback {

    private AtomicBoolean executed = new AtomicBoolean(false);

    public void checkAndExecute() throws ClientConnectionException {
        if (this.executed.compareAndSet(false, true)) {
            execute();
        }
    }

    public void resetExecution() {
        this.executed.compareAndSet(true, false);
    }

    public abstract void execute() throws ClientConnectionException;
}
