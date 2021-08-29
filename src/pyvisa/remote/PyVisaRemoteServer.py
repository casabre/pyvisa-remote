import sys
import pickle
import asyncio
import zmq
import zmq.asyncio
import logging
import msgpack
import pyvisa
import typing
import os
import json
from jsonschema import validate
from tblib import pickling_support
pickling_support.install()

LOGGER = logging.getLogger(__name__)

with open(os.path.abspath(os.path.join(os.path.dirname(__file__), '..',
                                       'data', 'jobs.schema.json'))) as fp:
    schema = json.load(fp)


class PyVisaRemoteServer(object):
    def __init__(self, port):
        super(PyVisaRemoteServer, self).__init__()
        self._ctx = zmq.asyncio.Context()
        self._socket = zmq.Socket(zmq.REP)
        self._socket.bind(f'tcp://*:{port}')
        self._visa_handle = dict()
        self._lock = asyncio.Lock()
        try:
            self._rm = pyvisa.ResourceManager()
        except Exception:
            self._rm = pyvisa.ResourceManager('@py')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, trace):
        self.close()

    def __del__(self):
        self.close()

    def close(self):
        if self._socket.is_open():
            self._socket.close()
        self._ctx.term()
        [x.close() for x in self._visa_handle.values()]

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        while True:
            msg = await self._socket.recv_multipart()
            reply = await self._call_pyvisa(msg)
            await self._socket.send_multipart(reply)

    async def _call_pyvisa(self, msg) -> zmq.Frame:
        address, empty, request = msg
        job_data = msgpack.loads(request)
        result = dict()
        try:
            validate(job_data, schema)
            res = await self._execute_job(job_data)
        except Exception:
            # Unfortunately, no simple and lightweight solution
            # https://stackoverflow.com/a/45241491
            result['exception'] = pickle.dumps(sys.exc_info())
        else:
            result['value'] = res
        return [address, b"", msgpack.dumps(result)]

    async def _execute_job(self, job_data: dict) -> \
            typing.Optional[typing.Any]:
        visa = await self._get_visa_handle(job_data['resource'])
        attribute = self._get_attr(
            visa, job_data['name'], )
        if job_data['action'] == '__getattr__':
            if callable(attribute):
                res = attribute(*job_data['args'], **job_data['kwargs'])
            else:
                res = attribute
        else:
            setattr(visa, job_data['name'], job_data['value'])
            res = None
        return res

    async def _get_visa_handle(self, resource: str) -> pyvisa.Resource:
        if resource in self._visa_handle:
            return self._visa_handle[resource]
        async with self._lock:
            self._visa_handle[resource] = self._rm.open_resource(resource)
        return self._visa_handle[resource]

    def _get_attr(visa: pyvisa.Resource, name: str):
        attr = getattr(visa, name)
        if not attr:
            raise AttributeError(f"module '{visa}' has no attribute '{name}'")
        return attr
