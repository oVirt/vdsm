package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACCEPT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACK;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;

import java.nio.channels.Selector;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.PlainClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

public class StompClient extends PlainClient {

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

        try {
            this.connected = new CountDownLatch(1);
            this.subscribed = new CountDownLatch(1);

            send(new Message().connect().withHeader(HEADER_ACCEPT, "1.2").build());
            this.connected.await(policy.getRetryTimeOut(), policy.getTimeUnit());
            this.subscribtionId = UUID.randomUUID().toString();
            send(new Message().subscribe().withHeader(HEADER_DESTINATION, RESPONSE_QUEUE)
                    .withHeader(HEADER_ID, this.subscribtionId).withHeader(HEADER_ACK, "client").build());
            // TODO wait for the mini broker to be finished
            // this.subscribed.await();
        } catch (InterruptedException e) {
            throw new ClientConnectionException("Timeout during connection", e);
        }
    }
}
