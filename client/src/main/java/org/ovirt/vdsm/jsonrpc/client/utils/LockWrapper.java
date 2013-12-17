package org.ovirt.vdsm.jsonrpc.client.utils;

import java.util.concurrent.locks.Lock;

/**
 * Lock wrapper object which allows to use try-finally block to auto unlock
 * internal <code>Lock</code>.
 *
 */
public class LockWrapper implements AutoCloseable {

    private Lock lock;

    public LockWrapper(Lock lock) {
        this.lock = lock;
        this.lock.lock();
    }

    @Override
    public void close() {
        this.lock.unlock();
    }

}
