import argparse
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Sequence, Union

import aioconsole
from async_upnp_client.aiohttp import AiohttpNotifyServer, AiohttpRequester
from async_upnp_client.client import UpnpDevice, UpnpService, UpnpStateVariable
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.profiles.dlna import DmrDevice, dlna_handle_notify_last_change
from async_upnp_client.utils import async_get_local_ip

_LOGGER = logging.getLogger("snapcast-upnp")

event_handler = None

_TIMEOUT = 4


async def _create_device(description_url: str) -> UpnpDevice:
    requester = AiohttpRequester(_TIMEOUT)
    factory = UpnpFactory(requester)
    return await factory.async_create_device(description_url)


def _get_timestamp() -> Union[str, float]:
    return datetime.now().isoformat(" ")


def _on_event(
    service: UpnpService, service_variables: Sequence[UpnpStateVariable]
) -> None:
    """Handle a UPnP event."""
    _LOGGER.debug(
        "State variable change for %s, variables: %s",
        service,
        ",".join([sv.name for sv in service_variables]),
    )
    obj = {
        "timestamp": _get_timestamp(),
        "service_id": service.service_id,
        "service_type": service.service_type,
        "state_variables": {sv.name: sv.value for sv in service_variables},
    }

    # special handling for DLNA LastChange state variable
    if len(service_variables) == 1 and service_variables[0].name == "LastChange":

        print(json.dumps(obj, indent=2))
        last_change = service_variables[0]
        dlna_handle_notify_last_change(last_change)
    else:
        print(json.dumps(obj, indent=2))


async def subscribe(description_url: str, service_names: Any) -> None:
    """Subscribe to service(s) and output updates."""
    global event_handler  # pylint: disable=global-statement

    device = await _create_device(description_url)

    # start notify server/event handler
    _, local_ip = await async_get_local_ip(device.device_url)
    server = AiohttpNotifyServer(device.requester, source=(local_ip, 0))
    await server.async_start_server()
    _LOGGER.debug("Listening on: %s", server.callback_url)

    # Create profile wrapper
    dmr_device = DmrDevice(device, server.event_handler)

    # TODO add event handler to device to get faster notification of playback changes?
    # self._device.on_event = self._on_event

    await dmr_device.async_subscribe_services(auto_resubscribe=True)

    while True:
        await asyncio.sleep(120)


async def upnp_task(args) -> None:

    await subscribe(args.device, "*")


async def snapcast_task():
    SNAPCAST_PLAYER_INTERFACE = "Plugin.Stream.Player"

    stdin, stdout = await aioconsole.get_standard_streams()
    while True:
        line = await stdin.readline()
        request = json.loads(line)

        interface, command = request["method"].split(".", 1)
        if interface != SNAPCAST_PLAYER_INTERFACE:
            _LOGGER.warning("Ignoring request for unknown '%s'.", interface)
            continue


async def _run(args):
    await asyncio.gather(
        upnp_task(args),
        snapcast_task(),
    )


# def main() -> None:
#     try:
#         asyncio.run(_run())
#     except KeyboardInterrupt:
#         # TODO
#         # if event_handler:
#         #     loop.run_until_complete(event_handler.async_unsubscribe_all())
#         pass


def main():
    # Basic log config
    logging.basicConfig(level=logging.DEBUG)

    # Argument parsing
    parser = argparse.ArgumentParser(
        description="Snapcast stream plugin for UPnP sources."
    )
    parser.add_argument("--verbose", help="Enable debug messages.", action="store_true")
    parser.add_argument("device", help="URL to device description XML")
    args = parser.parse_args()

    if args.verbose:
        _LOGGER.setLevel(logging.DEBUG)

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
