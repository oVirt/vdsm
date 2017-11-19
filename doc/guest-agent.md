oVirt Guest Agent
===================

In order to better manage the VMs we need to collect various statistics from
the guest OS, for example details about memory usage. For this purpose oVirt
Guest Agent (OGA) was developed. It also provides information that may be of
interest to the user, like free disk space or IP addresses assigned to the VM.

In short, the communication process can be described like this: When VDSM
starts new VM it finds out where is the socket pertaining to OGA. It connects
to the socket and waits for events sent to the socket. After the first
`heartbeat` event, it negotiates the effective API version. From here on VDSM
just listens on the socket and processes the events that are periodically sent
from OGA. When VDSM needs to issue a shutdown/reboot command invoke guest
hooks or make any other request to the OGA it sends the respective command to
the socket.

In VDSM the communication is (mostly) handled by two objects. The `Listener`
that serves the low-level communication with the OGA socket. `Listener`
instance is shared among all VMs. Second class is `GuestAgent` and it serves
the higher level purposes of parsing the OGA events or translating VDSM
requests to the appropriate commands. Every VM has its own instance of the
`GuestAgent` class.


QEMU Guest Agent
==================

In the long run we would like to deprecate the OGA and use only QEMU Guest
Agent (QGA) for communication with the guest OS. Realistically that may not
happen for a long, long time, because there are features that QGA will probably
always lack (e.g. hooks or SSO). Still, we plan to transition as much
functionality from OGA to QGA as possible.

From the VDSM point of view there are few issues though. We cannot use same or
similar approach that we use for communication to OGA. The two main reasons
are:

 -  We cannot (or don't have to) talk to the socket directly. Libvirt holds
    the connection to the socket because it needs to talk to QGA. All the
    communication has to go through libvirt. Right now, there is only the
    low-level function `virDomainQemuAgentCommand()`. It is unsupported and
    not meant to be used in production. This contradicts the libvirt
    philosophy that the management application should not care about details
    of anything from hypervisor down and libvirt should be the only point of
    contact. But at the moment it is the only way to access QGA. In the future
    we may be able to fetch the information by some more supported way, e.g.
    by `virConnectGetAllDomainStats()`

 -  QGA does not support events and there is no intention to implement that.
    This means there is no way how to request periodic information from QGA.
    Somebody has to regularly poll QGA for the information. Right now, this
    has to be VDSM for the above reason.


Class Relationships
=====================

Right now, the guest agent related code is scattered between several
components. The access to the classes is fragmented. There is one instance of
`Listener`, owned by `ClientIF`, that takes care of the low-level connections
to the OGA sockets. Then, there are multiple instances of `GuestAgent` owned
by VMs. One instance of `GuestAgent` per VM.


            clientIf --> Listener
                            ^
                            |
                            |
            VM(1)  ---------+---------  VM(2)
              ^             |             ^
              |             |             |
              v             |             v
        GuestAgent(1)  -----+-----  GuestAgent(2)




Note that the diagram is not truly complete. There is another class
`GuestAgentEvents`. But for the purposes this chapter tries to illustrate it
would only complicate the matters further. For those interested in details,
every `GuestAgent` object holds its own instance of `GuestAgentEvents` and
the VM calls its methods. The class serves a simple purpose -- it translates
the VM life-cycle events to the names of the OGA guest hooks.

The relationships get more complicated with the addition of QGA poller:


        QemuGuestAgentPoller <-- clientIf --> Listener
                 ^                               ^
                 |                               |
                 |                               |
                 |                VM(1) ---------+
                 |                  ^            |
                 |                  "            |
                 |                  v            |
                 +----------  GuestAgent(1)  ----+
                 |                               |
                 |                               |
                 |                VM(2) ---------+
                 |                  ^            |
                 |                  "            |
                 |                  v            |
                 +----------  GuestAgent(2)  ----+

                                    .
                                    .
                                    .


The code is scattered around VDSM too much. This makes it hard to change and
maintain during the transition period. Also, it makes it hard to understand
which component (OGA vs. QGA) is responsible source of the particular
information. Code should be refactored and the relevant classes joined
together to "shield" the internals from the rest of the system. The rest of
the code should not care where the information came from (OGA vs. QGA), nor
should it care how was it accessed (direct access to socket vs. libvirt).
Similarly, same should hold for the requests originating from VDSM -- whether
the request should be served by OGA or QGA (or both!) is just internal detail.


                      .....................................
                      .                                   .
                      .            ........................
                      .            . QemuGuestAgentPoller .
                      .            ........................
                      .                                   .
                      .                        ............
        ClientIf -->  . GuestAgentService      . Listener .
                      .                        ............
          VM(1)  -->  .                                   .
                      .              ......................
          VM(2)  -->  .              . OvirtGuestAgent(1) .
                      .              ......................
                      .              . OvirtGuestAgent(2) .
                      .              ......................
                      .                                   .
                      .....................................


In the above diagram the `GuestAgent` object has been renamed to
`OvirtGuestAgent` and everything has been wrapped in a new object
`GuestAgentService`. There would be single instance of this new object and it
would manage all the other necessary objects and bridge the requests from VMs
to the respective providers.
