package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACCEPT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACK;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_HEART_BEAT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_HOST;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_REPLY_TO;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.isEmpty;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.reduceGracePeriod;

import java.nio.channels.Selector;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;

import javax.net.ssl.SSLContext;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.SSLClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.AwaitRetry;

public class SSLStompClient extends SSLClient {

    private OneTimeCallback callback = new OneTimeCallback() {

        @Override
        public void execute() {
            if (connected.getCount() == 0) {
                connected = new CountDownLatch(1);
            }
            if (subscribed.getCount() == 0) {
                subscribed = new CountDownLatch(1);
            }

            subscribe(getResponseQueue());

            String eventQueue = getEventQueue();
            if (!isEmpty(eventQueue)) {
                subscribe(eventQueue);
            }

            Message message = new Message().connect().withHeader(HEADER_ACCEPT, "1.2").withHeader(HEADER_HOST,
                    policy.getIdentifier());
            int outgoing = 0;
            int incoming = 0;
            if (policy.isIncomingHeartbeat()) {
                incoming = policy.getIncomingHeartbeat();
            }
            if (policy.isOutgoingHeartbeat()) {
                outgoing = policy.getOutgoingHeartbeat();
            }
            if (incoming != 0 || outgoing != 0) {
                message.withHeader(HEADER_HEART_BEAT, outgoing + "," + reduceGracePeriod(incoming));
            }
            sendNow(message.build());
        }

        private void subscribe(String queueName) {
            String subId = UUID.randomUUID().toString();
            subscriptionIds.add(subId);
            sendNow(new Message().subscribe().withHeader(HEADER_DESTINATION, queueName)
                    .withHeader(HEADER_ID, subId).withHeader(HEADER_ACK, "auto").build());
        }

    };

    public SSLStompClient(Reactor reactor, Selector selector, String hostname, int port, SSLContext sslContext)
            throws ClientConnectionException {
        super(reactor, selector, hostname, port, sslContext);
        setWaitForConnect();
    }

    @Override
    public void sendMessage(byte[] message) throws ClientConnectionException {
        try {
            waitForConnect();
        } finally {
            send(new Message().send()
                    .withHeader(HEADER_DESTINATION, this.getRequestQueue())
                    .withHeader(HEADER_REPLY_TO, getResponseQueue())
                    .withCorrelationId()
                    .withContent(message)
                    .build());
        }
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        super.postConnect(getPostConnectCallback());
    }

    @Override
    protected OneTimeCallback getPostConnectCallback() {
        this.callback.resetExecution();
        setWaitForConnect();
        return this.callback;
    }

    private void setWaitForConnect() {
        this.connected = new CountDownLatch(1);
        this.subscribed = new CountDownLatch(1);
    }

    private void waitForConnect() throws ClientConnectionException {
        try {
            AwaitRetry.retry(() -> {
                connected.await(policy.getRetryTimeOut(), policy.getTimeUnit());
                return null;
            });
        } catch (Exception e) {
            log.error(e.getMessage(), e);
            disconnect("Waiting for connect failed");
            throw new IllegalStateException("Communication failed");
        }
    }

    @Override
    public boolean isInInit() {
        return this.nioEngine == null || this.nioEngine.handshakeInProgress();
    }
}
