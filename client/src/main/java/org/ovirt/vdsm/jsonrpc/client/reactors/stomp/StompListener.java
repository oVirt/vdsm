package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;

import java.io.IOException;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.CommandExecutor;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.Command;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Sender;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

public class StompListener extends StompClient implements Sender {
    private CommandFactory commandFactory;

    public StompListener(Reactor reactor, Selector selector, String hostname, int port, SocketChannel socketChannel)
            throws ClientConnectionException {
        super(reactor, selector, hostname, port);
        channel = socketChannel;
        this.commandFactory = new CommandFactory(this, eventListeners);

        postConnect(null);
    }

    @Override
    public void sendMessage(byte[] message) {
        send(new Message().message()
                .withHeader(HEADER_DESTINATION, RESPONSE_QUEUE)
                .withContent(new String(message, UTF8))
                .build());
    }

    void processMessage(Message message) {
        String command = message.getCommand();
        CommandExecutor executor = this.commandFactory.getCommandExecutor(command);
        Message response = executor.execute(message);
        if (response != null) {
            this.send(response.build());
        }
        if (Command.DISCONNECT.toString().equals(command)) {
            try {
                channel.close();
            } catch (IOException ignored) {
            }
        }
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        try {
            int interestedOps = SelectionKey.OP_READ;
            reactor.wakeup();
            key = this.channel.register(selector, interestedOps, this);
        } catch (ClosedChannelException e) {
            throw new ClientConnectionException(e);
        }
    }
}
