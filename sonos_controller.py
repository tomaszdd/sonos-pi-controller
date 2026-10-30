import requests
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import io
import evdev
from evdev import InputDevice, ecodes
import threading
import os

# Home Assistant connection: set these in the systemd unit (see sonos.service)
HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
TOKEN = os.environ["HA_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

SPEAKERS = [
    ("Bedroom", "media_player.main_bedroom"),
    ("Play:3",  "media_player.main_room"),
    ("SYMF",    "media_player.main_room_2"),
    ("Play:5",  "media_player.main_room_3"),
]

# Filled live from the Sonos "Radio" favourites each time the Source screen opens
SOURCES = []

W, H = 320, 240
FB = "/dev/fb0"
TOUCH_DEV = "/dev/input/event2"

BG     = (20, 20, 20)
PANEL  = (40, 40, 40)
ACCENT = (30, 215, 96)
WHITE  = (255, 255, 255)
GREY   = (150, 150, 150)
DARK   = (10, 10, 10)
RED    = (200, 50, 50)

RAW_X_MIN, RAW_X_MAX = 400, 3600
RAW_Y_MIN, RAW_Y_MAX = 400, 3600

touch = {"x": 0, "y": 0, "tapped": False}
touch_lock = threading.Lock()

# Screens
SCREEN_MAIN   = "main"
SCREEN_SOURCE = "source"

def map_touch(raw_x, raw_y):
    sx = W - int((raw_y - RAW_Y_MIN) / (RAW_Y_MAX - RAW_Y_MIN) * W)
    sy = int((raw_x - RAW_X_MIN) / (RAW_X_MAX - RAW_X_MIN) * H)
    sx = max(0, min(W-1, sx))
    sy = max(0, min(H-1, sy))
    return sx, sy

def touch_reader():
    try:
        dev = InputDevice(TOUCH_DEV)
        tx, ty = 0, 0
        for event in dev.read_loop():
            if event.type == ecodes.EV_ABS:
                if event.code == 0:
                    tx = event.value
                elif event.code == 1:
                    ty = event.value
            elif event.type == ecodes.EV_KEY and event.code == 330:
                if event.value == 1:
                    sx, sy = map_touch(tx, ty)
                    with touch_lock:
                        touch["x"] = sx
                        touch["y"] = sy
                        touch["tapped"] = True
    except Exception as e:
        print(f"Touch error: {e}")

def ha_get(entity_id):
    try:
        r = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=3)
        return r.json()
    except:
        return None

def ha_call(domain, service, entity_id, extra=None):
    data = {"entity_id": entity_id}
    if extra:
        data.update(extra)
    try:
        requests.post(f"{HA_URL}/api/services/{domain}/{service}",
                      headers=HEADERS, json=data, timeout=3)
    except:
        pass

def play_source(entity_id, content_id, content_type):
    try:
        r = requests.post(
            f"{HA_URL}/api/services/media_player/play_media",
            headers=HEADERS,
            json={"entity_id": entity_id, "media_content_id": content_id, "media_content_type": content_type},
            timeout=10
        )
        if r.status_code != 200:
            print(f"play_media failed {r.status_code}: {r.text[:200]}", flush=True)
    except Exception as e:
        print(f"play_media error: {e}", flush=True)

def fetch_sources(entity_id):
    """Load Sonos radio favourites live from HA; keep the current list on failure."""
    global SOURCES
    try:
        r = requests.post(
            f"{HA_URL}/api/services/media_player/browse_media?return_response",
            headers=HEADERS,
            json={"entity_id": entity_id, "media_content_id": "object.item.audioItem.audioBroadcast",
                  "media_content_type": "favorites_folder"},
            timeout=10
        )
        children = list(r.json()["service_response"].values())[0].get("children", [])
        if children:
            SOURCES = [(c["title"], c["media_content_id"], c["media_content_type"]) for c in children]
    except Exception as e:
        print(f"fetch_sources error: {e}", flush=True)

def fetch_album_art(url):
    try:
        if url.startswith("/"):
            url = HA_URL + url
        r = requests.get(url, headers=HEADERS, timeout=3)
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img = img.resize((80, 80))
        return img
    except:
        return None

def write_fb(img):
    arr = np.array(img)
    r = arr[:,:,0].astype(np.uint16)
    g = arr[:,:,1].astype(np.uint16)
    b = arr[:,:,2].astype(np.uint16)
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    with open(FB, 'wb') as f:
        f.write(rgb565.astype(np.uint16).tobytes())

def draw_rect(draw, rect, color, radius=6):
    draw.rounded_rectangle(rect, radius=radius, fill=color)

def get_fonts():
    try:
        return (
            ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12),
            ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14),
            ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18),
        )
    except:
        f = ImageFont.load_default()
        return f, f, f

def render_main(state, selected, album_art):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    font_sm, font_md, font_lg = get_fonts()

    # Speaker tabs
    tab_w = W // len(SPEAKERS)
    for i, (name, _) in enumerate(SPEAKERS):
        color = ACCENT if i == selected else PANEL
        txt_color = DARK if i == selected else GREY
        draw_rect(draw, [i*tab_w+2, 2, (i+1)*tab_w-2, 26], color)
        draw.text((i*tab_w + tab_w//2, 14), name, font=font_sm, fill=txt_color, anchor="mm")

    if state:
        attr = state.get("attributes", {})
        status = state.get("state", "unknown")
        title  = attr.get("media_title", "")
        artist = attr.get("media_artist", "")
        channel = attr.get("media_channel", "")
        volume = int(attr.get("volume_level", 0) * 100)
        muted  = attr.get("is_volume_muted", False)

        # Album art
        if album_art:
            img.paste(album_art, (235, 28))

        # Status
        dot_color = ACCENT if status == "playing" else GREY
        draw.ellipse([7, 33, 16, 42], fill=dot_color)
        status_text = ("MUTED" if muted else status.upper())
        draw.text((20, 30), status_text, font=font_sm, fill=(RED if muted else dot_color))

        # Track info
        display_title = title or channel or "Nothing playing"
        draw.text((5, 48), display_title[:22], font=font_lg, fill=WHITE)
        if artist:
            draw.text((5, 70), artist[:26], font=font_md, fill=GREY)
        elif channel and title:
            draw.text((5, 70), channel[:26], font=font_md, fill=GREY)

        # Volume bar
        draw_rect(draw, [5, 92, 225, 104], PANEL, radius=4)
        if volume > 0 and not muted:
            draw_rect(draw, [5, 92, 5 + int(220 * volume/100), 104], ACCENT, radius=4)
        vol_color = RED if muted else GREY
        draw.text((230, 90), f"{'M' if muted else ''}{volume}%", font=font_sm, fill=vol_color)
    else:
        draw.text((10, 50), "Connecting...", font=font_md, fill=GREY)

    # Row 1: Prev / Play-Pause / Next
    draw_rect(draw, [5, 112, 75, 152], PANEL)
    draw.text((40, 132), "|<", font=font_lg, fill=WHITE, anchor="mm")

    is_playing = state and state.get("state") == "playing"
    draw_rect(draw, [83, 112, 163, 152], ACCENT)
    draw.text((123, 132), "||" if is_playing else ">", font=font_lg, fill=DARK, anchor="mm")

    draw_rect(draw, [171, 112, 231, 152], PANEL)
    draw.text((201, 132), ">|", font=font_lg, fill=WHITE, anchor="mm")

    # Source button
    draw_rect(draw, [239, 112, 315, 152], (60, 60, 100))
    draw.text((277, 132), "Source", font=font_sm, fill=WHITE, anchor="mm")

    # Row 2: Vol- / Vol+ / Mute
    draw_rect(draw, [5, 160, 95, 200], PANEL)
    draw.text((50, 180), "Vol-", font=font_lg, fill=WHITE, anchor="mm")

    draw_rect(draw, [103, 160, 193, 200], PANEL)
    draw.text((148, 180), "Vol+", font=font_lg, fill=WHITE, anchor="mm")

    mute_color = RED if (state and state.get("attributes", {}).get("is_volume_muted")) else PANEL
    draw_rect(draw, [201, 160, 315, 200], mute_color)
    draw.text((258, 180), "Mute", font=font_lg, fill=WHITE, anchor="mm")

    return img

def render_source(scroll_offset):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    font_sm, font_md, font_lg = get_fonts()

    # Header
    draw_rect(draw, [0, 0, W, 28], (50, 50, 80), radius=0)
    draw.text((W//2, 14), "Select Source", font=font_md, fill=WHITE, anchor="mm")
    draw_rect(draw, [270, 3, 315, 25], RED)
    draw.text((292, 14), "Back", font=font_sm, fill=WHITE, anchor="mm")

    # Source buttons - 4 per screen, 2 columns
    visible = SOURCES[scroll_offset:scroll_offset+6]
    cols, rows = 2, 3
    btn_w = (W - 10) // cols
    btn_h = 58
    for idx, (name, _, _) in enumerate(visible):
        col = idx % cols
        row = idx // cols
        x1 = 5 + col * (btn_w + 2)
        y1 = 35 + row * (btn_h + 4)
        x2 = x1 + btn_w
        y2 = y1 + btn_h
        draw_rect(draw, [x1, y1, x2, y2], (50, 80, 120))
        draw.text(((x1+x2)//2, (y1+y2)//2), name, font=font_md, fill=WHITE, anchor="mm")

    # Scroll indicators
    if scroll_offset > 0:
        draw.text((W-15, 35), "^", font=font_sm, fill=GREY)
    if scroll_offset + 6 < len(SOURCES):
        draw.text((W-15, H-20), "v", font=font_sm, fill=GREY)

    return img

def handle_main_tap(mx, my, selected, state):
    tab_w = W // len(SPEAKERS)
    entity = SPEAKERS[selected][1]
    is_playing = state and state.get("state") == "playing"
    muted = state and state.get("attributes", {}).get("is_volume_muted", False)

    # Tabs
    if my < 28:
        return mx // tab_w, selected, SCREEN_MAIN, True

    # Prev
    if 112 <= my <= 152 and 5 <= mx <= 75:
        ha_call("media_player", "media_previous_track", entity)
        return selected, selected, SCREEN_MAIN, True

    # Play/Pause
    if 112 <= my <= 152 and 83 <= mx <= 163:
        ha_call("media_player", "media_pause" if is_playing else "media_play", entity)
        return selected, selected, SCREEN_MAIN, True

    # Next
    if 112 <= my <= 152 and 171 <= mx <= 231:
        ha_call("media_player", "media_next_track", entity)
        return selected, selected, SCREEN_MAIN, True

    # Source picker
    if 112 <= my <= 152 and 239 <= mx <= 315:
        fetch_sources(entity)
        return selected, selected, SCREEN_SOURCE, False

    # Vol-
    if 160 <= my <= 200 and 5 <= mx <= 95:
        if state:
            v = max(0, state["attributes"].get("volume_level", 0) - 0.05)
            ha_call("media_player", "volume_set", entity, {"volume_level": round(v, 2)})
        return selected, selected, SCREEN_MAIN, True

    # Vol+
    if 160 <= my <= 200 and 103 <= mx <= 193:
        if state:
            v = min(1, state["attributes"].get("volume_level", 0) + 0.05)
            ha_call("media_player", "volume_set", entity, {"volume_level": round(v, 2)})
        return selected, selected, SCREEN_MAIN, True

    # Mute
    if 160 <= my <= 200 and 201 <= mx <= 315:
        ha_call("media_player", "volume_mute", entity, {"is_volume_muted": not muted})
        return selected, selected, SCREEN_MAIN, True

    return selected, selected, SCREEN_MAIN, False

def handle_source_tap(mx, my, selected, scroll_offset):
    entity = SPEAKERS[selected][1]

    # Back button
    if my < 28 and mx >= 270:
        return SCREEN_MAIN, scroll_offset

    # Source buttons
    cols, rows = 2, 3
    btn_w = (W - 10) // cols
    btn_h = 58
    for idx in range(min(6, len(SOURCES) - scroll_offset)):
        col = idx % cols
        row = idx // cols
        x1 = 5 + col * (btn_w + 2)
        y1 = 35 + row * (btn_h + 4)
        x2 = x1 + btn_w
        y2 = y1 + btn_h
        if x1 <= mx <= x2 and y1 <= my <= y2:
            name, content_id, content_type = SOURCES[scroll_offset + idx]
            print(f"Playing: {name}")
            play_source(entity, content_id, content_type)
            return SCREEN_MAIN, scroll_offset

    # Scroll up
    if my < 60 and mx > W - 30 and scroll_offset > 0:
        return SCREEN_SOURCE, scroll_offset - 6

    # Scroll down
    if my > H - 30 and mx > W - 30 and scroll_offset + 6 < len(SOURCES):
        return SCREEN_SOURCE, scroll_offset + 6

    return SCREEN_SOURCE, scroll_offset

def main():
    t = threading.Thread(target=touch_reader, daemon=True)
    t.start()
    print("Sonos controller starting!")

    selected = 0
    last_refresh = 0
    state = None
    album_art = None
    last_art_url = None
    screen = SCREEN_MAIN
    scroll_offset = 0

    while True:
        now = time.time()
        if screen == SCREEN_MAIN and now - last_refresh > 3:
            state = ha_get(SPEAKERS[selected][1])
            last_refresh = now
            if state:
                art_url = state.get("attributes", {}).get("entity_picture")
                if art_url and art_url != last_art_url:
                    album_art = fetch_album_art(art_url)
                    last_art_url = art_url
                elif not art_url:
                    album_art = None
                    last_art_url = None

        with touch_lock:
            if touch["tapped"]:
                mx, my = touch["x"], touch["y"]
                touch["tapped"] = False

                if screen == SCREEN_MAIN:
                    new_sel, _, new_screen, refresh = handle_main_tap(mx, my, selected, state)
                    if new_sel != selected:
                        selected = new_sel
                        state = None
                        album_art = None
                        last_art_url = None
                    screen = new_screen
                    if refresh:
                        last_refresh = 0

                elif screen == SCREEN_SOURCE:
                    new_screen, scroll_offset = handle_source_tap(mx, my, selected, scroll_offset)
                    if new_screen == SCREEN_MAIN:
                        screen = SCREEN_MAIN
                        last_refresh = 0

        if screen == SCREEN_MAIN:
            img = render_main(state, selected, album_art)
        else:
            img = render_source(scroll_offset)

        write_fb(img)
        time.sleep(0.1)

if __name__ == "__main__":
    main()
