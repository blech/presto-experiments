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
FONT_SIZE = 32



def setup():
    presto = Presto(full_res=True)
    display = presto.display
    display.clear()
    presto.update()

    return presto, display


def get_image_list():
    print(f"Getting image list from {API_URL}")
    resp = urequests.get(API_URL)
    if resp.status_code != 200:
        return []
    images = json.loads(resp.content)
    print(f"... got {len(images)} images in response")
    return images


def fetch_images(presto, display, images):
    print("Saving images from list")
    image_paths = []

    for image in images:
        filename = image['image']
        capture_time = image['date']
        date, time = capture_time.split(' ')
        year, month, day = date.split('-')

        # pass image in to save function
        cache_image(presto, display, filename, year, month, day)

        image_paths.append(f"epic_images/{filename}.jpg")

    return image_paths


def cache_image(presto, display, filename, year, month, day):
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
    display_image(presto, display, image_path)

    return True


def display_image(presto, display, image_path):
    j = jpegdec.JPEG(display)

    j.open_file(image_path)
    j.decode(-30, -30, jpegdec.JPEG_SCALE_HALF)

    presto.update()


def display_text(presto, display, text, x=16, y=40):
    vector = PicoVector(presto.display)

    vector.set_font(FONT_PATH, FONT_SIZE)
    vector.set_font_word_spacing(120)
    vector.set_font_letter_spacing(90)

    pen = display.create_pen(255, 255, 255)
    display.set_pen(pen)
    vector.text(text, 16, 32) # baseline?
    presto.update()


def main():
    presto, display = setup()
    presto.connect()
    display_text(presto, display, "Fetching initial images")
    images = get_image_list()
    image_paths = fetch_images(presto, display, images)

    while True:
        for image_path in image_paths:
            display_image(presto, display, image_path)
            time.sleep(DISPLAY_REFRESH)


main()
