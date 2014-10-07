package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACCEPT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACK;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_HEART_BEAT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.reduceGracePeriod;

import java.nio.channels.Selector;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.PlainClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

public class StompClient extends PlainClient {

    private OneTimeCallback callback = new OneTimeCallback() {

        @Override
        public void execute() throws ClientConnectionException {
            try {
                connected = new CountDownLatch(1);
                subscribed = new CountDownLatch(1);

                Message message = new Message().connect().withHeader(HEADER_ACCEPT, "1.2");
                if (policy.isHeartbeat()) {
                    message.withHeader(HEADER_HEART_BEAT, 0 + "," + reduceGracePeriod(policy.getHeartbeat()));
                }
                send(message.build());

                subscribtionId = UUID.randomUUID().toString();
                send(new Message().subscribe().withHeader(HEADER_DESTINATION, RESPONSE_QUEUE)
                        .withHeader(HEADER_ID, subscribtionId).withHeader(HEADER_ACK, "client").build());

                connected.await(policy.getRetryTimeOut(), policy.getTimeUnit());
                // TODO wait for the mini broker to be finished
                // subscribed.await();
            } catch (InterruptedException e) {
                disconnect("Waiting for connect interrupted");
                throw new ClientConnectionException("Timeout during connection", e);
            }
        }
    };

    public StompClient(Reactor reactor, Selector selector, String hostname, int port)
            throws ClientConnectionException {
        super(reactor, selector, hostname, port);
    }

    @Override
    public void sendMessage(byte[] message) {
        send(new Message().send()
                .withHeader(HEADER_DESTINATION, REQUEST_QUEUE)
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
