package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;

import java.io.IOException;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLException;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.SSLEngineNioHelper;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.CommandExecutor;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.Command;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Sender;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

public class SSLStompListener extends SSLStompClient implements Sender {
    private static Log log = LogFactory.getLog(SSLStompListener.class);
    private CommandFactory commandFactory;

    public SSLStompListener(Reactor reactor, Selector selector, String hostname, int port,
            SocketChannel socketChannel, SSLContext sslContext) throws ClientConnectionException {
        super(reactor, selector, hostname, port, sslContext);

        channel = socketChannel;
        this.commandFactory = new CommandFactory(this, eventListeners);

        postConnect(null);
    }

    @Override
    public void sendMessage(byte[] message) {
        send(new Message().message()
                .withHeader(HEADER_DESTINATION, RESPONSE_QUEUE)
                .withContent(message)
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
                // we do not care about IOE after disconnecting
            }
        }
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        try {
            this.nioEngine = new SSLEngineNioHelper(channel, createSSLEngine(false), callback, this);
            this.nioEngine.beginHandshake();

            int interestedOps = SelectionKey.OP_READ;
            reactor.wakeup();
            key = this.channel.register(selector, interestedOps |= SelectionKey.OP_WRITE, this);
        } catch (ClosedChannelException | SSLException e) {
            log.error("Connection issues during ssl client creation", e);
            throw new ClientConnectionException(e);
        }
    }
}
