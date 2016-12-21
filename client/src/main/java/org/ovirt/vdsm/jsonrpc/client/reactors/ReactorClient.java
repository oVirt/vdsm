package org.ovirt.vdsm.jsonrpc.client.reactors;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.getTimeout;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.logException;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.SelectionKey;
import java.nio.channels.SocketChannel;
import java.rmi.ConnectException;
import java.security.cert.Certificate;
import java.util.Deque;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.FutureTask;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantLock;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.internal.ClientPolicy;
import org.ovirt.vdsm.jsonrpc.client.utils.LockWrapper;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.DefaultConnectionRetryPolicy;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.Retryable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Abstract implementation of <code>JsonRpcClient</code> which handles low level networking.
 *
 */
public abstract class ReactorClient {
    public interface MessageListener {
        public void onMessageReceived(byte[] message);
    }
    public static final String CLIENT_CLOSED = "Client close";
    public static final int BUFFER_SIZE = 1024;
    private static Logger log = LoggerFactory.getLogger(ReactorClient.class);
    private final String hostname;
    private final int port;
    private final Lock lock;
    private long lastIncomingHeartbeat = 0;
    private long lastOutgoingHeartbeat = 0;
    private AtomicBoolean closing = new AtomicBoolean();
    protected volatile ClientPolicy policy = new DefaultConnectionRetryPolicy();
    protected final List<MessageListener> eventListeners;
    protected final Reactor reactor;
    protected final Deque<ByteBuffer> outbox;
    protected SelectionKey key;
    protected ByteBuffer ibuff = null;
    protected SocketChannel channel;

    public ReactorClient(Reactor reactor, String hostname, int port) {
        this.reactor = reactor;
        this.hostname = hostname;
        this.port = port;
        this.eventListeners = new CopyOnWriteArrayList<MessageListener>();
        this.lock = new ReentrantLock();
        this.outbox = new ConcurrentLinkedDeque<>();
        this.closing.set(false);
    }

    public String getHostname() {
        return this.hostname;
    }

    public String getClientId() {
        String connectionHash = this.channel == null ? "" : Integer.toString(this.channel.hashCode());
        return this.hostname + ":" + connectionHash;
    }

    public void setClientPolicy(ClientPolicy policy) {
        this.validate(policy);
        this.policy = policy;
    }

    public ClientPolicy getRetryPolicy() {
        return this.policy;
    }

    public void connect() throws ClientConnectionException {
        if (isOpen()) {
            return;
        }
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            if (isOpen() && isInInit()) {
                getPostConnectCallback().await(policy.getRetryTimeOut(), policy.getTimeUnit());
            }
            if (isOpen()) {
                return;
            }
            final FutureTask<SocketChannel> task = scheduleTask(new Retryable<>(() -> {
                InetAddress address = InetAddress.getByName(hostname);
                log.info("Connecting to " + address);

                final InetSocketAddress addr = new InetSocketAddress(address, port);
                final SocketChannel socketChannel = SocketChannel.open();

                socketChannel.configureBlocking(false);
                socketChannel.connect(addr);

                return socketChannel;
            }, this.policy));
            this.channel = task.get();

            final long timeout = getTimeout(policy.getRetryTimeOut(), policy.getTimeUnit());
            while (!this.channel.finishConnect()) {

                final FutureTask<SocketChannel> connectTask = scheduleTask(new Retryable<>(() -> {
                    if (System.currentTimeMillis() >= timeout) {
                        throw new ConnectException("Connection timeout");
                    }
                    return null;
                }, this.policy));
                connectTask.get();
            }

            updateLastIncomingHeartbeat();
            updateLastOutgoingHeartbeat();
            if (!isOpen()) {
                throw new ClientConnectionException("Connection failed");
            }
            this.closing.set(false);
            postConnect(getPostConnectCallback());
        } catch (InterruptedException | ExecutionException e) {
            logException(log, "Exception during connection", e);
            final String message = "Connection issue " + e.getMessage();
            scheduleClose(message);
            throw new ClientConnectionException(e);
        } catch (IOException e) {
            closeChannel();
            throw new ClientConnectionException("Connection failed", e);
        }
    }

    public SelectionKey getSelectionKey() {
        return this.key;
    }

    public void addEventListener(MessageListener el) {
        eventListeners.add(el);
    }

    public void removeEventListener(MessageListener el) {
        eventListeners.remove(el);
    }

    protected void emitOnMessageReceived(byte[] message) {
        for (MessageListener el : eventListeners) {
            el.onMessageReceived(message);
        }
    }

    public final void disconnect(String message) {
        this.closing.set(true);
        byte[] response = buildNetworkResponse(message);
        postDisconnect();
        closeChannel();
        emitOnMessageReceived(response);
    }

    public Future<Void> close() {
        return scheduleClose(CLIENT_CLOSED);
    }

    private Future<Void> scheduleClose(final String message) {
        this.closing.set(true);
        return scheduleTask(() -> {
            disconnect(message);
            return null;
        });
    }

    protected <T> FutureTask<T> scheduleTask(Callable<T> callable) {
        final FutureTask<T> task = new FutureTask<>(callable);
        reactor.queueFuture(task);
        return task;
    }

    public void process() throws IOException, ClientConnectionException {
        processIncoming();
        if (this.closing.get()) {
            return;
        }
        processHeartbeat();
        if (this.closing.get()) {
            return;
        }
        processOutgoing();
    }

    /**
     * Process incoming channel.
     *
     * @throws IOException Thrown when reading issue occurred.
     * @throws ClientConnectionException  Thrown when issues with connection.
     */
    protected abstract void processIncoming() throws IOException, ClientConnectionException;

    private void processHeartbeat() {
        if (!this.isInInit() && this.policy.isIncomingHeartbeat() && this.isIncomingHeartbeatExeeded()) {
            log.debug("Heartbeat exceeded. Closing channel");
            this.disconnect("Heartbeat exceeded");
        }
    }

    private boolean isIncomingHeartbeatExeeded() {
        return this.lastIncomingHeartbeat + this.policy.getIncomingHeartbeat() < System.currentTimeMillis();
    }

    protected void updateLastIncomingHeartbeat() {
        this.lastIncomingHeartbeat = System.currentTimeMillis();
    }

    protected void updateLastOutgoingHeartbeat() {
        this.lastOutgoingHeartbeat = System.currentTimeMillis();
    }

    protected void processOutgoing() throws IOException, ClientConnectionException {
        final ByteBuffer buff = outbox.peekLast();

        if (buff == null) {
            return;
        }

        write(buff);

        if (!buff.hasRemaining()) {
            outbox.removeLast();
        }
        updateLastOutgoingHeartbeat();
        updateInterestedOps();
    }

    protected void closeChannel() {
        this.closing.set(true);
        final Callable<Void> callable = new Callable<Void>() {

            @Override
            public Void call() throws Exception {
                if (lock.tryLock()) {
                    try {
                        if (channel != null) {
                            channel.close();
                        }
                    } catch (IOException e) {
                        // Ignore
                    } finally {
                        channel = null;
                        lock.unlock();
                    }
                } else {
                    scheduleTask(this);
                }
                return null;
            }
        };
        try {
            callable.call();
        } catch (Exception e) {
            log.warn("Closing channel failed", e);
        }
    }

    public boolean isOpen() {
        return channel != null && channel.isOpen();
    }

    public int getConnectionId() {
        return Objects.hashCode(this.channel);
    }

    public void performAction() throws IOException, ClientConnectionException {
        if (!this.isInInit() && this.policy.isOutgoingHeartbeat() && this.isOutgoingHeartbeatExeeded()) {
            this.sendHeartbeat();
            this.processOutgoing();
        }
    }

    private boolean isOutgoingHeartbeatExeeded() {
        return this.lastOutgoingHeartbeat + this.policy.getOutgoingHeartbeat() < System.currentTimeMillis();
    }

    /**
     * Sends message using provided byte array.
     *
     * @param message - content of the message to sent.
     * @throws ClientConnectionException when issues with connection.
     */
    public abstract void sendMessage(byte[] message) throws ClientConnectionException;

    /**
     * Reads provided buffer.
     *
     * @param buff
     *            provided buffer to be read.
     * @return Number of bytes read.
     * @throws IOException
     *             when networking issue occurs.
     */
    protected abstract int read(ByteBuffer buff) throws IOException;

    /**
     * Writes provided buffer.
     *
     * @param buff
     *            provided buffer to be written.
     * @throws IOException
     *             when networking issue occurs.
     */
    abstract void write(ByteBuffer buff) throws IOException;

    /**
     * Transport specific post connection functionality.
     *
     * @param callback - callback which is executed after connection is estabilished.
     * @throws ClientConnectionException
     *             when issues with connection.
     */
    protected abstract void postConnect(OneTimeCallback callback) throws ClientConnectionException;

    /**
     * Updates selection key's operation set.
     */
    public abstract void updateInterestedOps();

    /**
     * @return Client specific {@link OneTimeCallback} or null. The callback is executed
     * after the connection is established.
     */
    protected abstract OneTimeCallback getPostConnectCallback();

    /**
     * Cleans resources after disconnect.
     */
    public abstract void postDisconnect();

    /**
     * @return <code>true</code> when connection initialization is in progress like
     *         SSL hand shake. <code>false</code> when connection is initialized.
     */
    public abstract boolean isInInit();

    /**
     * Builds network issue message for specific protocol.
     *
     * @param reason why we want to build network response.
     * @return byte array containing response.
     */
    protected abstract byte[] buildNetworkResponse(String reason);

    /**
     * Client sends protocol specific heartbeat message
     */
    protected abstract void sendHeartbeat();

    /**
     * Validates policy when it is set.
     *
     * @param policy - validated policy
     */
    public abstract void validate(ClientPolicy policy);

    /**
     * @return the peer certificates of the current session
     */
    public List<Certificate> getPeerCertificates() {
        throw new UnsupportedOperationException();
    }
}
