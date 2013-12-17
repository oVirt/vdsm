package org.ovirt.vdsm.jsonrpc.client;

import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.KeyStore;
import java.security.KeyStoreException;
import java.security.NoSuchAlgorithmException;
import java.security.UnrecoverableKeyException;
import java.security.cert.CertificateException;

import javax.net.ssl.KeyManager;
import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;

import org.ovirt.vdsm.jsonrpc.client.reactors.ManagerProvider;

public class TestManagerProvider extends ManagerProvider {

    private final static String KEY_STORE_FILE = "/home/pkliczewski/git/vdsm/tests/jsonrpc-tests.p12";
    private final static String TRUST_STORE_FILE = "/home/pkliczewski/git/vdsm/tests/jsonrpc-tests.p12";
    private final static String PASSWORD = "x";

    @Override
    public KeyManager[] getKeyManagers() {
        try (InputStream stream = new FileInputStream(KEY_STORE_FILE)) {
            KeyStore keyStore = KeyStore.getInstance("PKCS12");
            keyStore.load(stream, PASSWORD.toCharArray());
            KeyManagerFactory kmf = KeyManagerFactory.getInstance("SunX509");
            kmf.init(keyStore, PASSWORD.toCharArray());
            return kmf.getKeyManagers();
        } catch (NoSuchAlgorithmException | KeyStoreException | IOException
                | CertificateException | UnrecoverableKeyException e) {
            throw new RuntimeException(e);
        }
    }

    @Override
    public TrustManager[] getTustManagers() {
        try (InputStream stream = new FileInputStream(TRUST_STORE_FILE)) {
            KeyStore keyStore = KeyStore.getInstance("PKCS12");
            keyStore.load(stream, PASSWORD.toCharArray());
            TrustManagerFactory tmf = TrustManagerFactory.getInstance("SunX509");
            tmf.init(keyStore);
            return tmf.getTrustManagers();
        } catch (NoSuchAlgorithmException | IOException | KeyStoreException |
                CertificateException ex) {
            throw new RuntimeException(ex);
        }
    }

}
