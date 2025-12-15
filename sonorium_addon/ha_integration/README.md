# Sonorium Home Assistant Integration

This custom integration exposes Sonorium's 6 audio channels as `media_player` entities in Home Assistant.

## Installation

1. Copy the `custom_components/sonorium` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings > Devices & Services > Add Integration**
4. Search for "Sonorium" and configure with your Sonorium addon's host and port

## Entities Created

After setup, you'll have 6 media_player entities:

- `media_player.sonorium_channel_1`
- `media_player.sonorium_channel_2`
- `media_player.sonorium_channel_3`
- `media_player.sonorium_channel_4`
- `media_player.sonorium_channel_5`
- `media_player.sonorium_channel_6`

## Supported Features

Each channel entity supports:

- **Play**: Resume playing the current theme (if one was set)
- **Stop**: Stop playback on the channel
- **Select Source**: Choose a theme from the list of available themes
- **Play Media**: Play a specific theme by ID

## Example Dashboard Card

```yaml
type: media-control
entity: media_player.sonorium_channel_1
```

## Example Automations

### Play a theme on a channel
```yaml
service: media_player.select_source
target:
  entity_id: media_player.sonorium_channel_1
data:
  source: "Rain on Roof"
```

### Stop a channel
```yaml
service: media_player.media_stop
target:
  entity_id: media_player.sonorium_channel_1
```

### Play a theme by ID
```yaml
service: media_player.play_media
target:
  entity_id: media_player.sonorium_channel_1
data:
  media_content_type: music
  media_content_id: "rain_roof"
```

## Configuration

- **Host**: The hostname or IP where Sonorium is running (default: `localhost`)
- **Port**: The port Sonorium is listening on (default: `8009`)

If running Sonorium as a Home Assistant addon, use:
- Host: `localhost` or `homeassistant.local`
- Port: `8009`
