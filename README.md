## hass-welock

Home Assistant integration for welock based locks and wifibox.

### Overview
This integration uses the Welock Cloud and Wifibox to communicate with your lock. It supports the following features:

- Unlocking
- Lock battery level
- List records history

### Requirements

1. A WeLock based PCB,TOUCH,PBL ...
1. A wifibox/wifbox3 ...
1. The lock and wifibox are each already bound in the welock app.

### Setup via HACS (Home Assistant Community Store)
> [!IMPORTANT]
> Installation directly through HACS is not yet available because the integration is not yet official included into HACS. This process will take some time.

1. Find clientID and clientSecret via the home assistant icon on the left menu page in the welock app.
1. Install the extension [via HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=hzjchina&repository=hass-welock&category=integration) and restart Home Assistant.
<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=hzjchina&repository=hass-welock&category=Integration" target="_blank"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and open a repository inside the Home Assistant Community Store." width="" height=""></a>

### Entities

| Device | Entity | Description |
|---|---|---|
| Lock | lock | Unlock (the lock relocks automatically) |
| Lock / door sensor | Battery | Battery level in % |
| Lock / WIFIBOX3 | Last unlock | Method (card, fingerprint, passcode, app …); attributes `user`, `user_id`, `time` |
| Lock / WIFIBOX3 | Last activity | Timestamp of the last unlock |
| Lock / WIFIBOX3 | Activity (event) | Fires on every unlock (`unlock`) or relay action (`relay_action`) – ideal automation trigger |
| WIFIBOX3 | Trigger remote control | Button |
| Door sensor | Door | Open / closed |

### Options
*Settings → Devices & services → WeLock → Configure*: auto-relock delay (how long the lock is shown as unlocked, default 8 s).

### Changes in 2.0.0
- Fixed: all devices shared one status dict, values could appear on the wrong device
- Fixed: failed unlock / remote commands were reported as success
- Fixed: expired tokens (HTTP 401) now start re-authentication
- Fixed: the push (MQTT) connection reconnects automatically after errors
- Fixed: setup is retried when the cloud is unreachable at startup
- Fixed: no forced DEBUG logging, deprecated device-registry usage removed
- New: event entity, last-activity sensor, translated "last unlock" sensor restored after restart
- New: battery and device list refreshed every 30 minutes, new/removed devices handled automatically
- New: diagnostics download, German translation, test suite
- Existing entities keep their IDs – just update and restart

