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
    communication has to go through libvirt.

 -  QGA does not support events and there is no intention to implement that.
    This means there is no way how to request periodic information from QGA.
    Somebody has to regularly poll QGA for the information. Right now, this
    has to be VDSM for the above reason.


Libvirt interface to QEMU Guest Agent
=======================================

Libvirt holds the connection to the guest agent socket. That means all the
calls we need to do have to go through libvirt. Since libvirt 5.7.0 there is
generic interface to the agent information provided by
`virDomainGetGuestInfo()`. This function is not universal though. When new
commands are added to the agent libvirt has to be extended to call the new
command and provide the results. Some calls are handled by separate functions
and such information is not duplicated in `virDomainGetGuestInfo()`. Notably
`virDomainInterfaceAddresses()` that we use to retrieve information about NICs.

There is also the low-level function `virDomainQemuAgentCommand()`. It is
unsupported and not meant to be used in production. Using the function also
taints the domain. We need this function too though for several reasons.
Namely to receive general information about the agent (guest-info command)
with version and list of supported commands. But we also need to be able to
make calls not (yet) supported by libvirt interface.


Channel state tracking
========================

Because unlike the oVirt agent the QEMU guest agent is passive and does not
periodically report information from guest we have a hard time knowing if the
agent is in a good shape (i.e. is not stuck). That means if we want some
information or action from the agent we have to try and hope for the best.
Luckily we have at least some clue if the agent is there at all, listening for
commands or not (not running or not installed). Libvirt watches messages from
QEMU monitor about the state of the socket in the guest and tracks this
information internally. This information is then provided to the management
applications (in our case VDSM) in two ways -- in domain XML and via events.

Similarly we too keep the state of the agent stored internally in the poller.
We listen to libvirt `VIR_DOMAIN_EVENT_ID_AGENT_LIFECYCLE` event and remember
the state of the channel. In most of the cases this should be good enough for
tracking the actual state. Even during VM migration libvirt first notifies
VDSM on destination host that the agent is disconnected. Later, when the VM is
migrated, libvirt updates the state to connected if the agent is running
inside the guest. For the edge cases, when the events are not enough (e.g.
during VM recovery after VDSM restart) we can "bootstrap" the channel state by
reading it from domain XML.

Historically VDSM was blindly trying to reach the agent. This is still obvious
on some code paths, but should be eliminated over time.


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
