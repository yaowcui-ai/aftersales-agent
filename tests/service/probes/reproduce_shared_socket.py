"""Isolate Windows IOCP listener reuse without starting the project app."""

import asyncio
import multiprocessing
import socket


def register(sock, result):
    loop = asyncio.ProactorEventLoop()
    try:
        loop._proactor._register_with_iocp(sock)
        result.put({"result": "registered"})
    except OSError as error:
        result.put({"result": type(error).__name__, "winerror": error.winerror})
    finally:
        loop.close()
        sock.close()


if __name__ == "__main__":
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        for index in range(2):
            process = context.Process(target=register, args=(listener, result))
            process.start()
            try:
                print("CHILD", index + 1, result.get(timeout=10), flush=True)
                process.join(timeout=5)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
    result.close()
    result.join_thread()
