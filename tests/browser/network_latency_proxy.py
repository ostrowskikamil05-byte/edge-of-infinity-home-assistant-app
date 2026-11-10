"""Bounded loopback TCP delay for real HTTP/WebSocket browser benchmarks."""
import argparse
import asyncio
import json
from pathlib import Path
import signal


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend-port',type=int,required=True)
    parser.add_argument('--info',required=True)
    parser.add_argument('--delay-ms',type=int,default=100)
    args=parser.parse_args()
    if not 1<=args.backend_port<=65535 or not 0<=args.delay_ms<=1000:
        raise ValueError('invalid_test_proxy_options')
    loop=asyncio.get_running_loop()
    connections=set()

    async def forward(reader,writer):
        queue=asyncio.Queue(32)
        async def receive():
            try:
                while data:=await reader.read(65536):
                    await queue.put((loop.time()+args.delay_ms/1000,data))
            except (ConnectionError,OSError):pass
            finally:
                if not asyncio.current_task().cancelling():await queue.put(None)
        producer=asyncio.create_task(receive())
        try:
            while item:=await queue.get():
                due,data=item
                await asyncio.sleep(max(0,due-loop.time()))
                writer.write(data)
                await writer.drain()
        finally:
            producer.cancel()
            await asyncio.gather(producer,return_exceptions=True)

    async def connect(front,writer):
        task=asyncio.current_task();connections.add(task)
        back_writer=None;tasks=[]
        try:
            back,back_writer=await asyncio.open_connection('127.0.0.1',args.backend_port)
            tasks=[asyncio.create_task(forward(front,back_writer)),asyncio.create_task(forward(back,writer))]
            await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        except (ConnectionError,OSError):pass
        finally:
            for item in tasks:item.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            writer.close()
            if back_writer:back_writer.close()
            connections.discard(task)

    stopped=asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM,stopped.set)
    server=await asyncio.start_server(connect,'127.0.0.1',0)
    Path(args.info).write_text(json.dumps({'port':server.sockets[0].getsockname()[1]}))
    async with server:await stopped.wait()
    for task in list(connections):task.cancel()
    await asyncio.gather(*connections,return_exceptions=True)


asyncio.run(main())
