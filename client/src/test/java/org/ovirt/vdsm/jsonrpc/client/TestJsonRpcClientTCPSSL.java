package org.ovirt.vdsm.jsonrpc.client;

import org.junit.Ignore;

// TODO Test server do not support SSL yet

@Ignore
public class TestJsonRpcClientTCPSSL extends TestJsonRpcClient {

    @Override
    protected ReactorTestHelper getHelper() {
        return new TcpSSLReactorTestHelper();
    }
}
