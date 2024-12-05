# snapcast-upnp
Snapcast [stream plugin](https://github.com/badaix/snapcast/blob/develop/doc/json_rpc_api/stream_plugin.md) for UPnP renderers.

## Usage
1. Install stream plugin via pipx. 
    `pipx install .`
2. Add control script options to UPnP source in `snapserver.conf`.
    e.g.
    ```
    controlscript=/usr/local/bin/snapcast-upnp&controlscriptparams=<RENDERER_DESCRIPTION_URL>
    ```
    Where <RENDERER_DESCRIPTION_URL> is the network path to the renderer's device description XML. For gmrender-ressurect this would be `http://<RENDERER_IP>:59595/description.xml`.
3. Restart snapsever.