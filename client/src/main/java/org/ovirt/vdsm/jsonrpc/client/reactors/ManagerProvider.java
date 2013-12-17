package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.security.GeneralSecurityException;
import java.security.KeyManagementException;
import java.security.NoSuchAlgorithmException;

import javax.net.ssl.KeyManager;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;

/**
 * Provides abstraction for obtaining {@link TrustManager}s and {@link KeyManager}s.
 *
 */
public abstract class ManagerProvider {
    public abstract TrustManager[] getTustManagers() throws GeneralSecurityException;

    public abstract KeyManager[] getKeyManagers() throws GeneralSecurityException;

    public SSLContext getSSLContext() throws GeneralSecurityException {
        final SSLContext context;
        try {
            context = SSLContext.getInstance("TLS");
            context.init(getKeyManagers(), getTustManagers(), null);
        } catch (KeyManagementException | NoSuchAlgorithmException ex) {
            throw new RuntimeException(ex);
        }
        return context;
    }
}
