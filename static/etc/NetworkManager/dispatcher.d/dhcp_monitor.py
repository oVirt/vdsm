#!/usr/bin/python3

import ipaddress
import json
import os
import socket
import sys

UP_ACTIONS = ('up', 'dhcp4-change', 'dhcp6-change')

SOCKET_PATH = '/run/vdsm/dhcp-monitor.sock'


class ResponseField:
    IPADDR = 'ip'
    IPMASK = 'mask'
    IPROUTE = 'route'
    IFACE = 'iface'
    FAMILY = 'family'


class NMEnvVariables:
    ACTION = 'NM_DISPATCHER_ACTION'
    DEVICE_IFACE = 'DEVICE_IFACE'

    class DHCP4:
        ADDRESS = 'DHCP4_IP_ADDRESS'
        SUBNET_MASK = 'DHCP4_SUBNET_MASK'
        ROUTERS = 'DHCP4_ROUTERS'

    class IPV6:
        ADDR_LEN = 'IP6_NUM_ADDRESSES'
        ADDRESS_BASE = 'IP6_ADDRESS'


def main():
    action = os.getenv(NMEnvVariables.ACTION)
    if not action:
        return

    device = os.getenv(NMEnvVariables.DEVICE_IFACE)
    if action in UP_ACTIONS:
        handle_up(device)


def handle_up(device):
    dhcpv4 = os.getenv(NMEnvVariables.DHCP4.ADDRESS)
    ipv6_len = os.getenv(NMEnvVariables.IPV6.ADDR_LEN)
    if ipv6_len:
        ipv6_adresses = get_all_ipv6_addresses(int(ipv6_len))
        ipv6_address = get_global_ipv6_address(ipv6_adresses)
    else:
        ipv6_address = None

    if dhcpv4:
        mask = os.getenv(NMEnvVariables.DHCP4.SUBNET_MASK)
        route = os.getenv(NMEnvVariables.DHCP4.ROUTERS)
        content = create_up_content(dhcpv4, mask, device, route, 4)
        send_configuration(content)
    if ipv6_address:
        content = create_up_content(None, None, device, None, 6)
        send_configuration(content)


def get_all_ipv6_addresses(len):
    addresses = []
    for index in range(len):
        addresses.append(
            os.getenv(f'{NMEnvVariables.IPV6.ADDRESS_BASE}_{index}')
        )
    return addresses


def get_global_ipv6_address(adresses):
    return next(
        (address for address in adresses if not is_ipv6_link_local(address)),
        None,
    )


def is_ipv6_link_local(ipv6_address):
    ipv6, _ = ipv6_address.split(' ')
    return ipaddress.ip_interface(ipv6).is_link_local


def create_up_content(ip, mask, iface, route, family):
    return {
        ResponseField.FAMILY: family,
        ResponseField.IPADDR: ip,
        ResponseField.IPMASK: mask,
        ResponseField.IFACE: iface,
        ResponseField.IPROUTE: route,
    }


def send_configuration(content):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        try:
            client.connect(SOCKET_PATH)
            client.sendall(bytes(json.dumps(content), 'utf-8'))
        except FileNotFoundError:
            sys.exit(f'Cannot open {SOCKET_PATH} socket, vdsmd is not running')


if __name__ == '__main__':
    main()
