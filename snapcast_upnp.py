#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
from enum import StrEnum
from typing import Any, NoReturn, Sequence

import aioconsole
from async_upnp_client.aiohttp import AiohttpNotifyServer, AiohttpRequester
from async_upnp_client.client import UpnpService, UpnpStateVariable
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.profiles.dlna import DmrDevice, TransportState
from async_upnp_client.utils import async_get_local_ip

_LOGGER = logging.getLogger("snapcast-upnp")

_UPNP_TIMEOUT = 4


class SnapcastCommand(StrEnum):
    CONTROL = "Control"
    SET_PROPERTY = "SetProperty"
    GET_PROPERTIES = "GetProperties"


_SNAPCAST_PLAYER_INTERFACE = "Plugin.Stream.Player"
_SNAPCAST_SUPPORTED_COMMANDS = [e.value for e in SnapcastCommand]
_SNAPCAST_STREAM_READY = "Plugin.Stream.Ready"
_SNAPCAST_PLAYER_PROPERTIES = "Plugin.Stream.Player.Properties"

_TRANSPORT_STATE_TO_PLAYBACK_STATE = {
    TransportState.PLAYING: "playing",
    TransportState.TRANSITIONING: "playing",
    TransportState.PAUSED_PLAYBACK: "paused",
    TransportState.STOPPED: "stopped",
}


def get_properties(device) -> dict[str, Any]:
    props = {
        "playbackStatus": _TRANSPORT_STATE_TO_PLAYBACK_STATE.get(device.transport_state, "stopped"),
        "loopStatus": "none",  # TODO assume never looping
        "shuffle": False,  # TODO assume never shuffle
        "volume": int(100 * (device.volume_level or 0.0)) if device.has_volume_level else 100,
        "mute": device.is_volume_muted if device.has_volume_mute else False,
        "rate": 1.0,  # TODO assume always normal speed
        # TODO is not evented, need to poll, does snapcast use this?
        "position": device.media_position,

        "canGoNext": device.can_next,
        "canGoPrevious": device.can_previous,
        "canPlay": device.can_play,
        "canPause": device.can_pause,
        "canSeek": device.can_seek_abs_time and device.can_seek_rel_time,
        "canControl": device.can_stop or device.can_play,
    }

    if device.transport_state != TransportState.STOPPED:
        props["metadata"] = {
            "duration": device.media_duration or 0.0,
            "artist": [device.media_artist or ""],
            "album": device.media_album_name or "",
            "albumArtist": [device.media_album_artist or ""],
            "title": device.media_title or "",
            "artUrl": device.media_image_url or "",
            # TODO Original track number not supported by upnp_client_async
            # "trackNumber": device.media_original_track_number or "",
            # TODO Date not supported by upnp_client_async
            # "date": device.media_date or "",
        }
    else:
        props["metadata"] = None

    return props


async def send_control(command, device) -> None:
    control_methods = {
        "stop": device.async_stop,
        "play": device.async_play,
        "pause": device.async_pause,
        "playPause": (device.async_play if device.transport_state == TransportState.PAUSED_PLAYBACK else device.async_pause),
        "previous": device.async_previous,
        "next": device.async_next,
    }

    if method := control_methods.get(command):
        await method()


def jsonrpc_response(result, *, id=1) -> str:
    resp = {
        "id": id,
        "jsonrpc": "2.0",
        "result": result
    }

    return json.dumps(resp) + "\n"


def jsonrpc_command(method, params=None) -> str:
    cmd = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params:
        cmd["params"] = params

    return json.dumps(cmd) + "\n"


class DmrEventHandler:
    def __init__(self, device: DmrDevice, stdout) -> None:
        self._device = device
        self._stdout = stdout
        self._task = None

    async def _notify(self) -> None:
        # Delay to debounce events
        await asyncio.sleep(1.5)

        # Notify Snapcast
        notify = jsonrpc_command(
            _SNAPCAST_PLAYER_PROPERTIES, get_properties(self._device))
        self._stdout.write(notify)

        # Clear task object once we're done
        self._task = None

    def callback(self,
                 _service: UpnpService, state_variables: Sequence[UpnpStateVariable]
                 ) -> None:
        if not state_variables:
            return

        # Queue notify task for execution if it doesn't exist
        if self._task is None:
            self._task = asyncio.create_task(self._notify())


async def connect_device(description_url: str) -> DmrDevice:
    requester = AiohttpRequester(_UPNP_TIMEOUT)
    factory = UpnpFactory(requester)
    device = await factory.async_create_device(description_url)

    # Start notify server/event handler
    _, local_ip = await async_get_local_ip(device.device_url)
    server = AiohttpNotifyServer(device.requester, source=(local_ip, 0))
    await server.async_start_server()
    _LOGGER.debug("Listening on: %s", server.callback_url)

    # Create profile wrapper
    dmr_device = DmrDevice(device, server.event_handler)

    # Get async streams
    _, stdout = await aioconsole.get_standard_streams()

    # Create event handler
    event_handler = DmrEventHandler(dmr_device, stdout)

    # Subscribe to events
    dmr_device.on_event = event_handler.callback
    await dmr_device.async_subscribe_services(auto_resubscribe=True)

    return dmr_device


async def _run(args) -> NoReturn:
    device = await connect_device(args.device)
    await device.async_update()

    try:
        stdin, stdout = await aioconsole.get_standard_streams()

        # Indicate plugin is ready
        stdout.write(jsonrpc_command(_SNAPCAST_STREAM_READY))

        while True:
            line = await stdin.readline()
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                _LOGGER.error(
                    "Failed to decode input '%s' as JSON. Error: %s", line, e)
                continue

            _LOGGER.debug("Got request '%s'.", request)

            interface, command = request["method"].rsplit(".", 1)
            if interface != _SNAPCAST_PLAYER_INTERFACE:
                _LOGGER.warning(
                    "Ignoring request for unknown interface '%s'.", interface)
                continue

            if not command in _SNAPCAST_SUPPORTED_COMMANDS:
                _LOGGER.warning(
                    "Ignoring request for unsupported command '%s'.", command)
                continue

            if command == SnapcastCommand.GET_PROPERTIES:
                resp = jsonrpc_response(get_properties(device))
                stdout.write(resp)

            if command == SnapcastCommand.CONTROL:
                await send_control(request["params"]["command"], device)
                resp = jsonrpc_response("ok", id=request["id"])
                stdout.write(resp)

    except asyncio.CancelledError:
        _LOGGER.debug("Unsubscribing from services.")
        await device.async_unsubscribe_services()
        exit(0)


def main() -> None:
    # Basic log config
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    # Argument parsing
    parser = argparse.ArgumentParser(
        description="Snapcast stream plugin for UPnP sources."
    )
    parser.add_argument(
        "--verbose", help="Enable debug messages.", action="store_true")
    parser.add_argument("device", help="URL to device description XML")
    args, _unknown = parser.parse_known_args()

    if args.verbose:
        _LOGGER.setLevel(logging.DEBUG)

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
