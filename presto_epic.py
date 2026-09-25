# NAME Epic Viewer
# DESC EPIC viewer - see Earth
# ICON photo-library


"""
Fetch and display DSCOVER EPIC images of Earth
"""

import json
import os
import time

import jpegdec
import urequests

from presto import Presto
from picovector import PicoVector


DISPLAY_REFRESH = 20 # seconds
IMAGE_REFRESH = 120 # minutes

API_URL = "https://epic.gsfc.nasa.gov/api/natural"
IMAGE_URL_ROOT = "https://epic.gsfc.nasa.gov/archive/natural"

FONT_PATH = '/ocrb.af'
FONT_SIZE = 20

SHOW_INFO_AT_START = False


class EpicViewer():
    def __init__(self):
        self.presto = Presto(
            full_res=True,
            ambient_light=False,
        )
        self.display = self.presto.display

        self.vector = PicoVector(self.display)

        self.epic_data = []
        self.image_list = []
        self.show_info = SHOW_INFO_AT_START

        self.current_index = 0

        self.set_up_text()


    def connect(self):
        self.presto.connect()


    def wipe(self):
        self.black_pen()

        self.display.clear()
        self.presto.update()


    def refresh_images(self):
        self.display_text("Fetching image list", clear=True, refresh=True)
        self.get_image_list()

        self.display_text("Fetching images", clear=True, refresh=True)
        self.fetch_images()


    def get_image_list(self):
        print(f"Getting image list from {API_URL}")
        resp = urequests.get(API_URL)
        if resp.status_code != 200:
            return []
        self.epic_data = json.loads(resp.content)
        print(f"... got {len(self.epic_data)} images in response")


    def fetch_images(self):
        print("Saving images from list")

        y = 80

        for idx, image in enumerate(self.epic_data):
            filename = image['image']
            capture_time = image['date']
            date, time = capture_time.split(' ')
            year, month, day = date.split('-')

            # pass image in to save function
            image_path, fetched = self.cache_image(filename, year, month, day)
            if fetched:
                self.display_image(image_path, idx)
            else:
                y += FONT_SIZE * 1.25
                self.display_text(capture_time, 16, y)
                self.presto.update()

            self.image_list.append(image_path)


    def cache_image(self, filename, year, month, day):
        print(f"Saving image {filename} from {year}{month}{day}")

        image_path = f"epic_images/{filename}.jpg"

        try:
            os.stat(image_path)
            return image_path, False
        except OSError:
            pass

        image_url = f"{IMAGE_URL_ROOT}/{year}/{month}/{day}/jpg/{filename}.jpg"
        resp = urequests.get(image_url)
        with open(image_path, "wb") as f:
            f.write(resp.content)

        print("... file written")

        return image_path, True


    def display_images(self):
        while True:
            for idx, image_path in enumerate(self.image_list):
                now = time.ticks_ms()
                until =  time.ticks_add(now, DISPLAY_REFRESH*1000)
                self.display_image(image_path, idx)
                self.handle_touch_until(until)


    def handle_touch_until(self, until):
        was = False
        now = last = time.ticks_ms()
        last_ms = 0

        while time.ticks_diff(until, now) > 0:
            try:
                gap = time.ticks_diff(now, last)
                last = now

                self.presto.touch.poll()
                touched = self.presto.touch.state

                if touched and not was:
                    print("touch: down at", self.presto.touch.x, self.presto.touch.y,
                        "poll gap", gap, "ms")

                    now = time.ticks_ms()
                    since = time.ticks_diff(now, last_ms)
                    if since > 40:   # debounce
                        last_ms = now
                        print("touch: debounce passed, dispatching")
                        self.handle_touch(self.presto.touch)
                    else:
                        print("touch: debounced,", since, "ms since last accepted tap")
                was = touched
            except Exception as e:  # noqa: BLE001
                print("TOUCH ERROR:", repr(e))
            time.sleep_ms(20)
            now = time.ticks_ms()


    def handle_touch(self, touch):
        idx = self.current_index

        if self.show_info:
            self.show_info = False
            self.wipe_info(idx)
        else:
            self.show_info = True
            self.display_info(idx, with_update=True)


    def display_image(self, image_path, idx):
        self.current_index = idx

        j = jpegdec.JPEG(self.display)

        j.open_file(image_path)
        j.decode(-30, -30, jpegdec.JPEG_SCALE_HALF)

        if self.show_info:
            self.display_info(idx)
        self.presto.update()


    def display_info(self, idx, with_update=False):
        image_data = self.epic_data[idx]

        metrics = self._get_text_metrics(image_data)

        self.white_pen()

        self.vector.text(metrics['date'], metrics['dx'], 6+int(metrics['dh']))
        self.vector.text(metrics['loc'], metrics['lx'], 480-int(metrics['lh']))

        if with_update:
            self.presto.update()


    def _get_text_metrics(self, image_data):
        # date
        date = image_data['date']

        # latlong
        lat = image_data['centroid_coordinates']['lat']
        lon = image_data['centroid_coordinates']['lon']

        lat = f"{lat}N" if lat >= 0 else f"{abs(lat)}S"
        lon = f"{lon}E" if lon >= 0 else f"{abs(lon)}W"

        location = f"{lat} {lon}"

        _, _, dw, dh = self.vector.measure_text(date, x=0, y=0, angle=None)
        _, _, lw, lh = self.vector.measure_text(location, x=0, y=0, angle=None)
        dx = int((480-dw)/2)
        lx = int((480-lw)/2)

        return {
            'date': date,
            'dx': dx,
            'dh': dh,
            'dw': dw,
            'loc': location,
            'lx': lx,
            'lh': lh,
            'lw': lw,
        }


    def wipe_info(self, idx):
        image_data = self.epic_data[idx]

        metrics = self._get_text_metrics(image_data)

        # blank out means black pen
        self.black_pen()

        dx = int(metrics['dx'])
        dy = 6
        dw = int(metrics['dw'])
        dh = int(metrics['dh'])
        self.display.rectangle(dx-1, dy-1, dw+2, dh+2)

        lx = int(metrics['lx'])
        ly = 480 - 2*int(metrics['lh'])
        lw = int(metrics['lw'])
        lh = int(metrics['lh'])
        self.display.rectangle(lx-1, ly-1, lw+2, lh+2)

        self.presto.update()


    # Used to refresh the current image without needing to know what it is
    def redisplay_image(self):
        idx = self.current_index
        image_path = self.image_list[idx]
        self.display_image(image_path, idx)


    def white_pen(self):
        pen = self.display.create_pen(255, 255, 255)
        self.display.set_pen(pen)


    def black_pen(self):
        pen = self.display.create_pen(0, 0, 0)
        self.display.set_pen(pen)


    def set_up_text(self):
        self.vector.set_font(FONT_PATH, FONT_SIZE)
        self.vector.set_font_word_spacing(120)
        self.vector.set_font_letter_spacing(90)


    def display_text(self, text, x=16, y=40, clear=False, refresh=False):
        if clear:
            self.wipe()
            self.white_pen()

        self.vector.text(text, x, y)
        if refresh:
            self.presto.update()


def main():
    ev = EpicViewer()
    ev.display_text("Connecting", refresh=True)
    ev.connect()

    ev.refresh_images()

    ev.display_images()


main()
