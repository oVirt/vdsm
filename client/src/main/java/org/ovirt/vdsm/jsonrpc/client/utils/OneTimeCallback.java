package org.ovirt.vdsm.jsonrpc.client.utils;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicBoolean;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;

public abstract class OneTimeCallback {

    private AtomicBoolean executed = new AtomicBoolean(false);
    private CountDownLatch latch = new CountDownLatch(1);

    public void checkAndExecute() throws ClientConnectionException {
        if (this.executed.compareAndSet(false, true)) {
            execute();
            latch.countDown();
        }
    }

    public void resetExecution() {
        this.executed.compareAndSet(true, false);
    }

    public abstract void execute() throws ClientConnectionException;

    public void await() throws InterruptedException {
        this.latch.await();
    }
}
