"""Loaded only by test subprocesses through their explicit PYTHONPATH."""
import os
import socket
_original_getitem = os._Environ.__getitem__
def _getitem(env,key):
    if key=='API_KEY':raise AssertionError('test subprocess tried to read API_KEY')
    return _original_getitem(env,key)
def _network(*args,**kwargs):
    raise AssertionError('test subprocess tried to use the network')
os._Environ.__getitem__=_getitem
socket.socket=_network
socket.create_connection=_network
