package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACK;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_MESSAGE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_RECEIPT;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.MessageListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.CommandExecutor;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.Command;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Sender;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.ServerListener;
import org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils;

public class CommandFactory {

    private final Map<String, List<ServerListener>> listeners = new ConcurrentHashMap<>();
    private final Map<String, String> destinations = new ConcurrentHashMap<>();
    private final List<MessageListener> eventListeners;
    private final Sender sender;

    public CommandFactory(Sender sender, List<MessageListener> eventListeners) {
        this.sender = sender;
        this.eventListeners = eventListeners;
    }

    @SuppressWarnings("serial")
    public Map<String, CommandExecutor> commands = new HashMap<String, CommandExecutor>() {
        {
            put(Command.CONNECT.toString(), new CommandExecutor() {

                @Override
                public Message execute(Message message) {
                    return new Message().connected().withHeader("session", UUID.randomUUID().toString());
                }
            });
            put(Command.SUBSCRIBE.toString(), new CommandExecutor() {

                @Override
                public Message execute(Message message) {
                    Map<String, String> headers = message.getHeaders();
                    String destHeader = headers.get(HEADER_DESTINATION);
                    String idHeader = headers.get(HEADER_ID);

                    if (JsonUtils.isEmpty(destHeader) || JsonUtils.isEmpty(idHeader)) {
                        return new Message().error().withHeader(HEADER_MESSAGE, "Missing required header");
                    }
                    List<ServerListener> list = listeners.get(destHeader);
                    if (list == null) {
                        list = new CopyOnWriteArrayList<>();
                        listeners.put(destHeader, list);
                    }
                    list.add(new ServerListener(sender));
                    destinations.put(idHeader, destHeader);
                    if (!JsonUtils.isEmpty(headers.get(HEADER_ACK))) {
                        return new Message().ack().withHeader(HEADER_ID, idHeader);
                    }
                    return null;
                }
            });
            put(Command.SEND.toString(), new CommandExecutor() {

                @Override
                public Message execute(Message message) {
                    Map<String, String> headers = message.getHeaders();
                    String destHeader = headers.get(HEADER_DESTINATION);

                    if (JsonUtils.isEmpty(destHeader)) {
                        return new Message().error().withHeader(HEADER_MESSAGE, "Missing required header");
                    }

                    for (MessageListener el : eventListeners) {
                        el.onMessageReceived(message.getContent());
                    }

                    return null;
                }
            });
            put(Command.UNSUBSCRIBE.toString(), new CommandExecutor() {

                @Override
                public Message execute(Message message) {
                    Map<String, String> headers = message.getHeaders();
                    String idHeader = headers.get(HEADER_ID);

                    if (JsonUtils.isEmpty(idHeader)) {
                        return new Message().error().withHeader(HEADER_MESSAGE, "Missing required header");
                    }
                    String destination = destinations.remove(idHeader);
                    listeners.remove(destination);
                    if (!JsonUtils.isEmpty(headers.get(HEADER_ACK))) {
                        return new Message().ack().withHeader(HEADER_ID, idHeader);
                    }
                    return null;
                }
            });
            put(Command.DISCONNECT.toString(), new CommandExecutor() {

                @Override
                public Message execute(Message message) {
                    Map<String, String> headers = message.getHeaders();
                    String receipt = headers.get(HEADER_RECEIPT);

                    if (JsonUtils.isEmpty(receipt)) {
                        return new Message().error().withHeader(HEADER_MESSAGE, "Missing required header");
                    }
                    return new Message().receipt().withHeaders(headers);
                }
            });
        }
    };

    public CommandExecutor getCommandExecutor(String command) {
        return this.commands.get(command);
    }
}
