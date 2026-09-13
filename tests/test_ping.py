from aiohttp import ClientSession, WSMsgType, web


async def test_aiohttp_autoping_echoes_once_without_client_keepalive(unused_tcp_port: int) -> None:
    observed: list[tuple[WSMsgType, bytes]] = []

    async def handler(request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(autoping=False)
        await socket.prepare(request)
        await socket.ping(b"binance-payload")
        message = await socket.receive(timeout=2)
        observed.append((message.type, message.data))
        try:
            extra = await socket.receive(timeout=0.1)
            observed.append((extra.type, extra.data))
        except TimeoutError:
            pass
        await socket.close()
        return socket

    app = web.Application()
    app.router.add_get("/ws", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()
    try:
        async with ClientSession() as session:
            async with session.ws_connect(
                f"http://127.0.0.1:{unused_tcp_port}/ws", autoping=True, heartbeat=None
            ) as socket:
                while not socket.closed:
                    await socket.receive()
    finally:
        await runner.cleanup()
    assert observed == [(WSMsgType.PONG, b"binance-payload")]
