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
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.PlainClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.AwaitRetry;

public class StompClient extends PlainClient {

    private OneTimeCallback callback = new OneTimeCallback() {

        @Override
        public void execute() throws ClientConnectionException {
            connected = new CountDownLatch(1);
            subscribed = new CountDownLatch(1);

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

            try {
                AwaitRetry.retry(new Callable<Void>() {

                    @Override
                    public Void call() throws Exception {
                        connected.await(policy.getRetryTimeOut(), policy.getTimeUnit());
                        return null;
                    }

                });
            } catch (Exception e) {
                disconnect("Waiting for connect interrupted");
                throw new ClientConnectionException("Timeout during connection", e);
            }
        }

        private void subscribe(String queueName) {
            String subId = UUID.randomUUID().toString();
            subscriptionIds.add(subId);
            sendNow(new Message().subscribe().withHeader(HEADER_DESTINATION, queueName)
                    .withHeader(HEADER_ID, subId).withHeader(HEADER_ACK, "auto").build());
        }
    };

    public StompClient(Reactor reactor, Selector selector, String hostname, int port)
            throws ClientConnectionException {
        super(reactor, selector, hostname, port);
    }

    @Override
    public void sendMessage(byte[] message) {
        send(new Message().send()
                .withHeader(HEADER_DESTINATION, this.getRequestQueue())
                .withHeader(HEADER_REPLY_TO, getResponseQueue())
                .withCorrelationId()
                .withContent(message)
                .build());
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        super.postConnect(callback);

        callback.execute();
    }

    @Override
    protected OneTimeCallback getPostConnectCallback() {
        this.callback.resetExecution();
        return this.callback;
    }

    @Override
    public boolean isInInit() {
        return false;
    }
}
