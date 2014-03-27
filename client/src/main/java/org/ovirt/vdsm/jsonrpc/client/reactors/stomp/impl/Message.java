package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.isEmpty;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;

public class Message {
    public enum Command {
        SEND,
        SUBSCRIBE,
        UNSUBSCRIBE,
        BEGIN,
        COMMIT,
        ABORT,
        DISCONNECT,
        CONNECT,
        RECEIPT,
        CONNECTED,
        ERROR,
        ACK,
        MESSAGE;
    }
    private static final Log LOG = LogFactory.getLog(Message.class);
    public static final String HEADER_DESTINATION = "destination";
    public static final String HEADER_ACCEPT = "accept-version";
    public static final String HEADER_ID = "id";
    public static final String HEADER_MESSAGE = "message";
    public static final String HEADER_ACK = "ack";
    public static final String HEADER_TRANSACTION = "transaction";
    public static final String HEADER_RECEIPT = "receipt";
    public static final String HEADER_RECEIPT_ID = "receipt-id";
    private static final String END_OF_MESSAGE = "\000";
    private String command;
    private Map<String, String> headers = new HashMap<>();
    private String content;

    public Message withHeader(String key, String value) {
        this.headers.put(key, value);
        return this;
    }

    public Message withHeaders(Map<String, String> headers) {
        this.headers.putAll(headers);
        return this;
    }

    public Message withContent(String content) {
        this.content = content;
        return this;
    }

    public Message send() {
        this.command = Command.SEND.toString();
        return this;
    }

    public Message ack() {
        this.command = Command.ACK.toString();
        return this;
    }

    public Message subscribe() {
        this.command = Command.SUBSCRIBE.toString();
        return this;
    }

    public Message unsubscribe() {
        this.command = Command.UNSUBSCRIBE.toString();
        return this;
    }

    public Message begin() {
        this.command = Command.BEGIN.toString();
        return this;
    }

    public Message commit() {
        this.command = Command.COMMIT.toString();
        return this;
    }

    public Message abort() {
        this.command = Command.ABORT.toString();
        return this;
    }

    public Message disconnect() {
        this.command = Command.DISCONNECT.toString();
        return this;
    }

    public Message connect() {
        this.command = Command.CONNECT.toString();
        return this;
    }

    public Message receipt() {
        this.command = Command.RECEIPT.toString();
        return this;
    }

    public Message connected() {
        this.command = Command.CONNECTED.toString();
        return this;
    }

    public Message error() {
        this.command = Command.ERROR.toString();
        return this;
    }

    public Message message() {
        this.command = Command.MESSAGE.toString();
        return this;
    }

    private Message setCommand(String command) {
        this.command = command;
        return this;
    }

    public byte[] build() {
        if (isEmpty(this.command)) {
            throw new IllegalArgumentException("Command can't be empty");
        }
        StringBuilder builder = new StringBuilder(this.command);
        builder.append("\n");

        for (String key : this.headers.keySet()) {
            builder.append(key);
            builder.append(":");
            builder.append(this.headers.get(key));
            builder.append("\n");
        }

        builder.append("\n");

        if (!isEmpty(this.content)) {
            builder.append(this.content);
        }

        builder.append(END_OF_MESSAGE + "\n");

        return builder.toString().getBytes(UTF8);
    }

    public String getCommand() {
        return command;
    }

    public Map<String, String> getHeaders() {
        return headers;
    }

    public String getContent() {
        return content;
    }

    public static List<Message> buildMessages(String message) {
        String[] messageLines = message.split("\n");
        List<Message> results = new ArrayList<>();
        if (messageLines.length == 0) {
            return results;
        }
        try {
            int i = 0;
            while (i < messageLines.length - 1) {
                Message result = new Message();
                Command parsedCommand = Command.valueOf(messageLines[i]);
                result.setCommand(parsedCommand.toString());
                Map<String, String> headers = new HashMap<>();
                String currentLine = messageLines[++i];
                while (currentLine.length() > 0) {
                    int ind = currentLine.indexOf(':');
                    String key = currentLine.substring(0, ind);
                    String value = currentLine.substring(ind + 1, currentLine.length());
                    headers.put(key.trim(), value.trim());
                    currentLine = messageLines[++i];
                }
                result.withHeaders(headers);
                i++;
                StringBuilder content = new StringBuilder();
                String endLine = null;
                for (int k = i; k < messageLines.length; k++, i++) {
                    String line = messageLines[k];
                    if (line.contains(END_OF_MESSAGE)) {
                        int idx = line.indexOf(END_OF_MESSAGE);
                        content.append(line.substring(0, idx));
                        endLine = line.substring(idx + 1, line.length());
                        break;
                    } else {
                        content.append(line);
                    }
                }
                result.withContent(content.toString());
                results.add(result);
                if (!isEmpty(endLine)) {
                    messageLines[i] = endLine;
                } else {
                    i++;
                }
            }
        } catch (IllegalArgumentException e) {
            LOG.warn("Not recognized command type");
        }
        return results;
    }
}
