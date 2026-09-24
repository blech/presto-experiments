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
FONT_SIZE = 24


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
        self.show_info = True

        self.set_up_pen()
        self.set_up_text()


    def connect(self):
        self.presto.connect()


    def wipe(self):
        pen = self.display.create_pen(0, 0, 0)
        self.display.set_pen(pen)

        self.display.clear()
        self.presto.update()

        pen = self.display.create_pen(255, 255, 255)
        self.display.set_pen(pen)


    def get_image_list(self):
        print(f"Getting image list from {API_URL}")
        resp = urequests.get(API_URL)
        if resp.status_code != 200:
            return []
        self.epic_data = json.loads(resp.content)
        print(f"... got {len(self.epic_data)} images in response")


    def fetch_images(self):
        print("Saving images from list")

        for image in self.epic_data:
            filename = image['image']
            capture_time = image['date']
            date, time = capture_time.split(' ')
            year, month, day = date.split('-')

            # pass image in to save function
            self.cache_image(filename, year, month, day)

            self.image_list.append(f"epic_images/{filename}.jpg")


    def cache_image(self, filename, year, month, day):
        print(f"Saving image {filename} from {year}{month}{day}")

        image_path = f"epic_images/{filename}.jpg"

        try:
            os.stat(image_path)
            return True
        except OSError:
            pass

        image_url = f"{IMAGE_URL_ROOT}/{year}/{month}/{day}/jpg/{filename}.jpg"
        resp = urequests.get(image_url)
        with open(image_path, "wb") as f:
            f.write(resp.content)

        print("... file written")
        self.display_image(image_path)

        return True


    def display_images(self):
        while True:
            for idx, image_path in enumerate(self.image_list):
                self.display_image(image_path, idx)
                time.sleep(DISPLAY_REFRESH)


    def display_image(self, image_path, idx):
        j = jpegdec.JPEG(self.display)

        j.open_file(image_path)
        j.decode(-30, -30, jpegdec.JPEG_SCALE_HALF)

        if self.show_info:
            image_data = self.epic_data[idx]

            # date
            date = image_data['date']

            # latlong
            lat = image_data['centroid_coordinates']['lat']
            lon = image_data['centroid_coordinates']['lon']

            lat = f"{lat}N" if lat >= 0 else f"{abs(lat)}S"
            lon = f"{lon}E" if lon >= 0 else f"{abs(lon)}W"

            location = f"{lat} {lon}"

            pen = self.display.create_pen(255, 255, 255)
            self.display.set_pen(pen)

            _, _, dw, dh = self.vector.measure_text(date, x=0, y=0, angle=None)
            _, _, lw, lh = self.vector.measure_text(location, x=0, y=0, angle=None)
            dx = int((480-dw)/2)
            lx = int((480-lw)/2)

            self.vector.text(date, dx, 4+int(dh))
            self.vector.text(location, lx, 480-int(lh))

        self.presto.update()


    def set_up_pen(self):
        # set up pen - since we only use a single colour, only do this once
        pen = self.display.create_pen(255, 255, 255)
        self.display.set_pen(pen)


    def set_up_text(self):
        self.vector.set_font(FONT_PATH, FONT_SIZE)
        self.vector.set_font_word_spacing(120)
        self.vector.set_font_letter_spacing(90)


    def display_text(self, text, x=16, y=40, clear=False, refresh=False):
        if clear:
            self.wipe()
        self.vector.text(text, x, y)
        if refresh:
            self.presto.update()


def main():
    ev = EpicViewer()
    ev.display_text("Connecting", refresh=True)
    ev.connect()
    ev.display_text("Fetching initial images", clear=True, refresh=True)
    ev.get_image_list()
    ev.fetch_images()
    ev.display_images()


main()
