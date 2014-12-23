package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.SSLStompClientTestCase.createProvider;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.SSLContext;

import org.junit.Ignore;
import org.junit.experimental.theories.DataPoint;
import org.junit.experimental.theories.Theories;
import org.junit.experimental.theories.Theory;
import org.junit.runner.RunWith;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.TestManagerProvider;
import org.ovirt.vdsm.jsonrpc.client.internal.ClientPolicy;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener.EventListener;

// Takes a long time to finish
@Ignore
@RunWith(Theories.class)
public class HeartbeatTestCase {

    private final static String HOSTNAME = "localhost";
    private final static int WAIT_TIMEOUT = 10;

    @DataPoint
    public static int heartbeat_1 = 3000;

    @DataPoint
    public static int heartbeat_2 = 0;

    @Theory
    public void testSSLHeartbeat(int incoming, int outgoing) {
        TestManagerProvider provider = null;
        Reactor listeningReactor = null;
        Reactor sendingReactor = null;
        try {
            provider = createProvider();
            SSLContext context = provider.getSSLContext();
            listeningReactor = new SSLStompReactor(context);
            sendingReactor = new SSLStompReactor(context);

            testHeartbeat(listeningReactor, sendingReactor, incoming, outgoing);
        } catch (GeneralSecurityException | IOException | ClientConnectionException | InterruptedException
                | ExecutionException e) {
            fail();
        } finally {
            if (provider != null) {
                provider.closeStreams();
                provider = null;
            }
            if (sendingReactor != null) {
                try {
                    sendingReactor.close();
                } catch (IOException ignored) {
                }
            }
            if (listeningReactor != null) {
                try {
                    listeningReactor.close();
                } catch (IOException ignored) {
                }
            }
        }
    }

    @Theory
    public void testPlainHeartbeat(int incoming, int outgoing) {
        Reactor listeningReactor = null;
        Reactor sendingReactor = null;

        try {
            listeningReactor = new StompReactor();
            sendingReactor = new StompReactor();

            this.testHeartbeat(listeningReactor, sendingReactor, incoming, outgoing);
        } catch (IOException | ClientConnectionException | InterruptedException | ExecutionException e) {
            fail();
        } finally {
            if (sendingReactor != null) {
                try {
                    sendingReactor.close();
                } catch (IOException ignored) {
                }
            }
            if (listeningReactor != null) {
                try {
                    listeningReactor.close();
                } catch (IOException ignored) {
                }
            }
        }
    }

    private ReactorClient listeningClient = null;

    private void testHeartbeat(Reactor listeningReactor, Reactor sendingReactor, int incoming, int outgoing)
            throws ClientConnectionException,
            InterruptedException, ExecutionException {
        Future<ReactorListener> futureListener =
                listeningReactor.createListener(HOSTNAME, 0, new EventListener() {

                    @Override
                    public void onAcccept(final ReactorClient client) {
                        listeningClient = client;
                    }
                });

        ReactorListener listener = futureListener.get();
        assertNotNull(listener);

        ReactorClient client = sendingReactor.createClient(HOSTNAME, listener.getPort());
        client.setClientPolicy(new ClientPolicy(180000, 0, incoming, outgoing));
        client.connect();

        TimeUnit.SECONDS.sleep(WAIT_TIMEOUT);

        assertTrue(client.isOpen());
        assertTrue(this.listeningClient.isOpen());

        client.close();
        listener.close();
        this.listeningClient = null;
    }

}
