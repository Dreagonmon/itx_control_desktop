"""
pyboard interface

This module provides the Pyboard class, used to communicate with and
control a MicroPython device over a communication channel. Only real
boards is supported.
Must be a serial connection.

Example usage:

    import pyboard
    pyb = pyboard.Pyboard('/dev/ttyACM0')
    # pyb = pyboard.Pyboard('COM3') # for windows

Then:

    pyb.enter_raw_repl()
    pyb.exec('import pyb')
    pyb.exec('pyb.LED(1).on()')
    pyb.exit_raw_repl()

"""
import time
import serial
import json
import traceback
import asyncio
import io
from collections import deque
from inspect import isawaitable
from serial.tools.list_ports import comports


def get_possible_devices():
    return [port.device for port in comports(False)]


class ResultQueueListener(asyncio.Lock):
    def __init__(self) -> None:
        self.__ev = asyncio.Event()  # asyncio.Event
        self.__res = deque()  # return result
        self.__bgt: set[asyncio.Task] = set()  # background task
        self.__lock = asyncio.Lock()
        self.__canceled = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        async with self.__lock:
            if self.__canceled:
                raise StopAsyncIteration
            if len(self.__res) <= 0:
                task = asyncio.create_task(self.__ev.wait())
                self.__bgt.add(task)
                task.add_done_callback(self.__bgt.discard)
                try:
                    await task
                except asyncio.CancelledError:
                    raise StopAsyncIteration
                self.__ev.clear()
            return self.__res.popleft()

    def callback(self, value):
        self.__res.append(value)
        self.__ev.set()

    async def cancel(self):
        self.__canceled = True
        if len(self.__bgt) > 0:
            for task in self.__bgt:
                task.cancel()
            await asyncio.gather(*self.__bgt, return_exceptions=True)


class ResultEventListener:
    def __init__(self) -> None:
        self.__ev = asyncio.Event()  # asyncio.Event
        self.__ret = None  # return result
        self.__bgt: set[asyncio.Task] = set()  # background task
        self.__lock = asyncio.Lock()

    async def wait(self, timeout=None):
        # may raise CancelledError and TimeoutError (asyncio)
        async with self.__lock:
            task = asyncio.create_task(
                asyncio.wait_for(self.__ev.wait(), timeout))
            self.__bgt.add(task)
            task.add_done_callback(self.__bgt.discard)
            await task
            self.__ev.clear()
            return self.__ret

    def callback(self, value):
        self.__ret = value
        self.__ev.set()

    async def cancel(self):
        if len(self.__bgt) > 0:
            for task in self.__bgt:
                task.cancel()
            await asyncio.gather(*self.__bgt, return_exceptions=True)


class SerialProtocol:
    def __init__(self) -> None:
        self.__s = None  # serial
        self.__v = 0  # protocol version
        self.__l: dict[str, set[ResultEventListener]] = dict()  # listeners
        self.__lt = None  # listener async task
        self.__lock = asyncio.Lock()

    @property
    def version(self):
        return self.__v

    def add_listener(self, event: str, listener):
        assert hasattr(listener, "callback") and callable(
            getattr(listener, "callback"))
        assert hasattr(listener, "cancel") and callable(
            getattr(listener, "cancel"))
        if event not in self.__l.keys():
            self.__l[event] = set()
        self.__l[event].add(listener)

    def remove_listener(self, event: str, listener):
        if event in self.__l.keys():
            self.__l[event].discard(listener)

    async def close(self):
        if self.__lt:
            self.__lt.cancel()
            # let the listened do the rest
            try:
                await self.__lt
            except asyncio.CancelledError:
                pass

    async def write(self, data: str | bytearray | bytes):
        async with self.__lock:
            if isinstance(data, str):
                data = data.encode("utf8")
            view = memoryview(data)
            pointer = 0
            size = len(data)
            while pointer < size:
                n = self.__s.write(view[pointer : pointer + 128])
                pointer += n
                await asyncio.sleep(0.0)

    async def read_until(self, end: bytes = b"\n", timeout=None):
        buffer = bytearray()
        before_sec = time.time()
        while self.__s != None:
            data = self.__s.read(1)
            buffer.extend(data)
            now_sec = time.time()
            if buffer.endswith(end) or (isinstance(timeout, (int, float)) and (now_sec - before_sec > timeout)):
                return bytes(buffer)
            await asyncio.sleep(0.0)

    async def flush_input(self):
        n = self.__s.in_waiting
        while n > 0:
            self.__s.read(n)
            n = self.__s.in_waiting
            await asyncio.sleep(0.0)

    async def trigger_listener(self, event: str, data: dict):
        tasks = set()
        if event in self.__l:
            listeners = self.__l.get(event)
            for listener in listeners:
                result = listener.callback(data)
                if isawaitable(result):
                    tasks.add(result)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel_all_listener(self):
        tasks = set()
        for event in self.__l:
            listeners = self.__l.get(event)
            for listener in listeners:
                result = listener.cancel()
                if isawaitable(result):
                    tasks.add(result)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def request(self, event: str, data: dict = {}):
        data["event"] = event
        await self.write(json.dumps(data))
        await self.write("\r\n")

    async def request_with_result(self, event: str, data: dict = {}, timeout=None):
        listener = ResultEventListener()
        return_event_name = event + "_return"
        self.add_listener(return_event_name, listener)
        try:
            await self.request(event, data)
            data = await listener.wait(timeout)
            return data
        finally:
            self.remove_listener(return_event_name, listener)

    async def event_iter(self, event: str):
        listener = ResultQueueListener()
        self.add_listener(event, listener)
        try:
            async for data in listener:
                yield data
        finally:
            self.remove_listener(event, listener)

    async def _clean_up(self):
        event = "closed"
        data = {
            "event": event
        }
        await self.trigger_listener(event, data)
        await self.cancel_all_listener()
        if self.__s and not self.__s.closed:
            self.__s.close()
            self.__s = None
        self.__lt = None
        self.__v = 0

    async def _listen(self):
        await self.flush_input()
        data = bytes()
        while self.__s != None:
            try:
                data = await self.read_until()
                resp = json.load(io.BytesIO(data))
                event = resp["event"]
                await self.trigger_listener(event, resp)
            except (asyncio.CancelledError, KeyboardInterrupt):
                break
            except serial.SerialException:
                # disconnected
                print("read failed, maybe disconnected.")
                break
            except json.JSONDecodeError:
                print("JSON_ERROR", data)
            except:
                traceback.print_exc(1)
            await asyncio.sleep(0.0)
        # clean up
        await self._clean_up()

    async def _init(self, port: str, baudrate: int = 115200):
        try:
            self.__s = serial.Serial(
                port, baudrate=baudrate, timeout=0, write_timeout=0)
            if not self.__s.is_open:
                self.__s.open()
        except OSError:
            # traceback.print_exc(1)
            return False
        # init
        await self.write("\r\n")
        self.__lt = asyncio.create_task(self._listen())
        try:
            resp = await self.request_with_result("protocol_version", {}, 0.25)
            self.__v = resp["version"]
        except:
            # traceback.print_exc(1)
            await self.close()
            return False
        return True

    async def init(self, port: str = "", baudrate: int = 115200):
        if port == "":
            # try every port
            for port in comports(False):
                if await self._init(port.device, baudrate):
                    return True
            return False
        else:
            return await self._init(port, baudrate)

class SerialDevice(SerialProtocol):
    async def dialog(self, text="", title="", text_yes="OK", text_no="OK"):
        req = {
            "text": text,
            "title": title,
            "text_yes": text_yes,
            "text_no": text_no,
        }
        data = await self.request_with_result("dialog", req)
        return data["value"]
    
    async def select_menu(self, text="", title="", options = ["Empty"], text_yes="OK", text_no="CANCEL"):
        assert len(options) > 0
        req = {
            "text": text,
            "title": title,
            "options": options,
            "text_yes": text_yes,
            "text_no": text_no,
        }
        data = await self.request_with_result("select_menu", req)
        return data["value"]
    
    async def select_list(self, title="", options = ["Empty"], text_yes="OK", text_no="CANCEL"):
        assert len(options) > 0
        req = {
            "title": title,
            "options": options,
            "text_yes": text_yes,
            "text_no": text_no,
        }
        data = await self.request_with_result("select_list", req)
        return data["value"]

    async def input_text(self, text="", title="Edit Text"):
        req = {
            "text": text,
            "title": title,
        }
        data = await self.request_with_result("input_text", req)
        return data["value"]
