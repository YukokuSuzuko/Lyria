"""Explicit pytest plugin: permit loopback only, including DNS resolution.

Opt in with -p tests.ai_offline_guard. It never persists OS/network settings.
"""
import ipaddress
import socket

_originals = {}


def _check_host(host):
    if host is None:
        return
    if isinstance(host, bytes):
        host = host.decode('ascii')
    if str(host).lower() == 'localhost':
        return
    try:
        if ipaddress.ip_address(str(host)).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError('Offline AI tests forbid non-loopback network access')


def pytest_sessionstart(session):
    _originals.update(connect=socket.socket.connect, connect_ex=socket.socket.connect_ex,
                      getaddrinfo=socket.getaddrinfo)
    def connect(sock, address):
        if isinstance(address, tuple): _check_host(address[0])
        return _originals['connect'](sock, address)
    def connect_ex(sock, address):
        if isinstance(address, tuple): _check_host(address[0])
        return _originals['connect_ex'](sock, address)
    def resolve(host, *args, **kwargs):
        _check_host(host)
        return _originals['getaddrinfo'](host, *args, **kwargs)
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, resolve


def pytest_sessionfinish(session, exitstatus):
    socket.socket.connect = _originals['connect']
    socket.socket.connect_ex = _originals['connect_ex']
    socket.getaddrinfo = _originals['getaddrinfo']
