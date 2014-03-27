package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import java.util.Map;

public interface Listener {
    void update(String content);
    void error(Map<String, String> error);
}
