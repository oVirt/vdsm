package org.ovirt.vdsm.jsonrpc.client;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
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

    private final static String KEY_STORE_FILE = "/home/pkliczewski/git/vdsm-jsonrpc-java/client/src/test/resources/jsonrpc-tests.p12";
    private final static String TRUST_STORE_FILE = "/home/pkliczewski/git/vdsm-jsonrpc-java/client/src/test/resources/jsonrpc-tests.p12";
    private final static String PASSWORD = "x";
    private InputStream keyStream;
    private InputStream trustStream;
    private String pass;

    public TestManagerProvider() throws FileNotFoundException {
        this(KEY_STORE_FILE, TRUST_STORE_FILE, PASSWORD);
    }

    public TestManagerProvider(String keyStorePath, String trustStorePath, String pass) throws FileNotFoundException {
        this(new FileInputStream(new File(keyStorePath)), new FileInputStream(new File(trustStorePath)), pass);
    }

    public TestManagerProvider(InputStream keyStream, InputStream trustStream, String pass) {
        this.keyStream = keyStream;
        this.trustStream = trustStream;
        this.pass = pass;
    }

    @Override
    public KeyManager[] getKeyManagers() {
        try {
            KeyStore keyStore = KeyStore.getInstance("PKCS12");
            keyStore.load(this.keyStream, this.pass.toCharArray());
            KeyManagerFactory kmf = KeyManagerFactory.getInstance("SunX509");
            kmf.init(keyStore, this.pass.toCharArray());
            return kmf.getKeyManagers();
        } catch (NoSuchAlgorithmException | KeyStoreException | IOException
                | CertificateException | UnrecoverableKeyException e) {
            throw new RuntimeException(e);
        }
    }

    @Override
    public TrustManager[] getTrustManagers() {
        try {
            KeyStore keyStore = KeyStore.getInstance("JKS");
            keyStore.load(this.trustStream, this.pass.toCharArray());
            TrustManagerFactory tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            tmf.init(keyStore);
            return tmf.getTrustManagers();
        } catch (NoSuchAlgorithmException | IOException | KeyStoreException |
                CertificateException ex) {
            throw new RuntimeException(ex);
        }
    }

    public void closeStreams() {
        if (this.trustStream != null) {
            try {
                this.trustStream.close();
            } catch (IOException ignored) {
            }
        }
        if (this.keyStream != null) {
            try {
                this.keyStream.close();
            } catch (IOException ignored) {
            }
        }
    }
}
