package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.security.GeneralSecurityException;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.internal.ResponseWorker;

/**
 * Factory class which provide single instance of <code>Reactor</code>s or
 * <code>ResponseWorker</code> within single loading scope.
 *
 */
public class ReactorFactory {

    private static volatile NioReactor nioReactor;
    private static volatile SSLReactor sslReactor;
    private static volatile ResponseWorker worker;

    /**
     * Provides instance of <code>Reactor</code> based on <code>ManagerProvider</code>
     * availability.
     * @param provider - Provides ability to get SSL context.
     * @return <code>NioReactor</code> reactor when provider is <code>null</code> or
     *         <code>SSLReactor</code>.
     * @throws ClientConnectionException
     */
    public static Reactor getReactor(ManagerProvider provider) throws ClientConnectionException {
        if (provider == null) {
            return getNioReactor();
        }
        return getSslReactor(provider);
    }

    /**
     * @return Single instance of <code>ResponseWorker</code>.
     */
    public static ResponseWorker getWorker() {
        if (worker != null) {
            return worker;
        }
        synchronized (ReactorFactory.class) {
            if (worker != null) {
                return worker;
            }
            worker = new ResponseWorker();
        }
        return worker;
    }

    private static Reactor getNioReactor() throws ClientConnectionException {
        if (nioReactor != null) {
            return nioReactor;
        }
        synchronized (ReactorFactory.class) {
            if (nioReactor != null) {
                return nioReactor;
            }
            try {
                nioReactor = new NioReactor();
            } catch (IOException e) {
                throw new ClientConnectionException(e);
            }
        }
        return nioReactor;
    }

    private static Reactor getSslReactor(ManagerProvider provider) throws ClientConnectionException {
        if (sslReactor != null) {
            return sslReactor;
        }
        synchronized (ReactorFactory.class) {
            if (sslReactor != null) {
                return sslReactor;
            }
            try {
                sslReactor = new SSLReactor(provider.getSSLContext());
            } catch (IOException | GeneralSecurityException e) {
                throw new ClientConnectionException(e);
            }
        }
        return sslReactor;
    }
}
