package org.ovirt.vdsm.jsonrpc.client.reactors;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.logException;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.security.cert.Certificate;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLEngine;
import javax.net.ssl.SSLException;
import javax.net.ssl.SSLPeerUnverifiedException;
import javax.net.ssl.SSLSession;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompCommonClient;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.Retryable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * <code>ReactorClient</code> implementation to provide encrypted communication.
 *
 */
public abstract class SSLClient extends StompCommonClient {
    private static Logger log = LoggerFactory.getLogger(SSLClient.class);
    protected final Selector selector;
    protected SSLEngineNioHelper nioEngine;
    private SSLContext sslContext;
    private boolean client;

    public SSLClient(Reactor reactor, Selector selector, String hostname, int port, SSLContext sslctx)
            throws ClientConnectionException {
        super(reactor, hostname, port);
        this.selector = selector;
        this.sslContext = sslctx;
        this.client = true;
    }

    public SSLClient(Reactor reactor,
            Selector selector,
            String hostname,
            int port,
            SSLContext sslctx,
            SocketChannel socketChannel) throws ClientConnectionException {
        super(reactor, hostname, port);
        this.selector = selector;
        this.sslContext = sslctx;
        this.client = false;
        channel = socketChannel;

        postConnect(null);
    }

    protected SSLEngine createSSLEngine(boolean clientMode) {
        final SSLEngine engine = this.sslContext.createSSLEngine();
        engine.setUseClientMode(clientMode);
        return engine;
    }

    @Override
    public void updateInterestedOps() {
        if (outbox.isEmpty() && (this.nioEngine == null || !this.nioEngine.handshakeInProgress())) {
            getSelectionKey().interestOps(SelectionKey.OP_READ);
        } else {
            getSelectionKey().interestOps(SelectionKey.OP_READ | SelectionKey.OP_WRITE);
        }
    }

    private Runnable pendingOperations() throws IOException, ClientConnectionException {
        if (this.nioEngine == null) {
            return null;
        }

        return nioEngine.process();
    }

    @Override
    protected int read(ByteBuffer buff) throws IOException {
        if (this.nioEngine != null) {
            return this.nioEngine.read(buff);
        } else {
            return channel.read(buff);
        }
    }

    @Override
    void write(ByteBuffer buff) throws IOException {
        if (this.nioEngine != null) {
            this.nioEngine.write(buff);
        } else {
            channel.write(buff);
        }
    }

    @Override
    public void process() throws IOException, ClientConnectionException {
        final Runnable op = pendingOperations();
        if (op != null) {
            key.interestOps(0);
            scheduleTask(new Callable<Void>() {
                @Override
                public Void call() {
                    op.run();
                    updateInterestedOps();
                    selector.wakeup();
                    return null;
                }
            });
        }

        if (isInInit()) {
            return;
        }
        super.process();
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        try {
            final ReactorClient client = this;
            final FutureTask<SelectionKey> task = scheduleTask(new Retryable<SelectionKey>(
                    new Callable<SelectionKey>() {

                        @Override
                        public SelectionKey call() throws ClosedChannelException {
                            if (!SSLClient.this.isOpen()) {
                                throw new ClosedChannelException();
                            }
                            return channel.register(selector, SelectionKey.OP_READ | SelectionKey.OP_WRITE , client);
                        }
                    }, this.policy));

            key = task.get();

            SSLEngine sslEngine = createSSLEngine(this.client);
            this.nioEngine = new SSLEngineNioHelper(channel, sslEngine, callback, this);
            this.nioEngine.beginHandshake();
        } catch (SSLException | InterruptedException | ExecutionException e) {
            logException(log, "Connection issues during ssl client creation", e);
            throw new ClientConnectionException(e);
        }
        if (key == null) {
            throw new ClientConnectionException("Connection issue during post connect");
        }
    }

    @Override
    public void postDisconnect() {
        if (this.nioEngine != null) {
            this.nioEngine.clearBuff();
        }
        outbox.clear();
        this.nioEngine = null;
    }

    public List<Certificate> getPeerCertificates() {
        try {
            if (nioEngine != null && nioEngine.getSSLEngine() != null) {
                SSLSession sslSession = nioEngine.getSSLEngine().getSession();
                if (sslSession == null || !sslSession.isValid()) {
                    throw new IllegalStateException("SSL session is invalid");
                }
                return Arrays.asList(sslSession.getPeerCertificates());
            }
        } catch (SSLPeerUnverifiedException e) {
            logException(log, "Failed to get peer certificates", e);
        }

        return null;
    }
}
