# Sonos Pi Controller

A small touchscreen remote for Sonos speakers, built from a Raspberry Pi and a 2.8" Adafruit PiTFT. It talks to Sonos through the Home Assistant REST API. It draws straight to the framebuffer with Pillow, so it needs no X11 and no desktop.

## What it does
- **Speaker tabs** along the top: tap one to switch which speaker you control.
- **Now playing**: shows the title, artist or station, album art and a volume bar, refreshed every 3 seconds.
- **Controls**: previous, play/pause, next, volume down, volume up and mute.
- **Source picker**: lists your Sonos **Radio favourites**, loaded live from Home Assistant each time you open it, two pages if there are more than six. Tap one to play it.

## Hardware
- Raspberry Pi (tested on a Model B) with a WiFi dongle or Ethernet
- Adafruit PiTFT 2.8" resistive touchscreen (ILI9340 display, STMPE610 touch, SPI)

`/boot/firmware/config.txt`:
```
dtparam=spi=on
dtoverlay=pitft28-resistive,rotate=90,speed=32000000,fps=20
```

## Install
```bash
sudo apt install python3-requests python3-numpy python3-pil python3-evdev fonts-dejavu-core
cp sonos_controller.py ~/
sudo cp sonos.service /etc/systemd/system/
sudo nano /etc/systemd/system/sonos.service   # set HA_URL and HA_TOKEN
sudo systemctl enable --now sonos
```
Edit `SPEAKERS` in `sonos_controller.py` to match your `media_player.*` entity IDs. The touch device path is `TOUCH_DEV`, which defaults to `/dev/input/event2`.

## Lessons from Home Assistant + Sonos
- **Play Sonos favourites, not raw stream links.** If you call `media_player.play_media` with a service link such as `x-sonosapi-hls:…`, Sonos rejects it with `UPnP Error 402: Invalid Args`, because it needs metadata that Home Assistant doesn't send. Favourites work every time:
  1. `browse_media` with `media_content_type: favorites_folder` and `media_content_id: object.item.audioItem.audioBroadcast` returns the Radio favourites.
  2. `play_media` with `media_content_type: favorite_item_id` and `media_content_id: FV:2/<n>` plays one.
- To read the response from `browse_media` over REST, POST to `/api/services/media_player/browse_media?return_response`.

## Touch calibration
The raw touch range is roughly 400–3600 on both axes. With the screen rotated 90°, X and Y swap:
`sx = W - (raw_y - 400) / 3200 * W`, `sy = (raw_x - 400) / 3200 * H`.

## Licence
MIT
