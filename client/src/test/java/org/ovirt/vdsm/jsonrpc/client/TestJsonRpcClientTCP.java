package org.ovirt.vdsm.jsonrpc.client;

import org.junit.Ignore;

//Time consuming test
@Ignore 
public class TestJsonRpcClientTCP extends TestJsonRpcClient {

    @Override
    protected ReactorTestHelper getHelper() {
        return new TcpReactorTestHelper();
    }

}
