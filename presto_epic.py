import json
import os
import time

from presto import Presto
import jpegdec
import urequests


DISPLAY_REFRESH = 20 # seconds
IMAGE_REFRESH = 120 # minutes

API_URL = "https://epic.gsfc.nasa.gov/api/natural"
IMAGE_URL_ROOT = "https://epic.gsfc.nasa.gov/archive/natural"


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


def main():
    presto, display = setup()
    presto.connect()
    images = get_image_list()
    image_paths = fetch_images(presto, display, images)

    while True:
        for image_path in image_paths:
            display_image(presto, display, image_path)
            time.sleep(DISPLAY_REFRESH)


main()

#
#
#
# Traceback (most recent call last):
#   File "<stdin>", line 1, in <module>
# AttributeError: 'module' object has no attribute 'open_file'
# img = j.open_file('epic_images/00.jpg')
# j.decode(0, 0, jpegdec.JPEG_SCALE_FULL)
# True
# display.update()
#
# display.update
# <bound_method>
# display.update()
# display = picographics.PicoGraphics(display=picographics.DISPLAY_PICO_EXPLORER)
# Traceback (most recent call last):
#   File "<stdin>", line 1, in <module>
# NameError: name 'picographics' isn't defined
# import presto;
# display.clear()
# display = presto.display
# display.clear()
# display.update()
# BLACK = display.create_pen(0, 0, 0)
# display.clear()
# display.update()
# presto.update()
# img = j.open_file('epic_images/00.jpg')
# j.decode(0, 0, jpegdec.JPEG_SCALE_FULL)
# True
# presto.update()
# display.update()
# presto.update()
# j = jpegdec.JPEG(display)
# img = j.open_file('epic_images/00.jpg')
# j.decode(0, 0, jpegdec.JPEG_SCALE_FULL)
# True
# display.update()
# presto.update()
#
# presto.connect()
# True
# data = json.loads(resp.content)
# date = data[-1]['date']
# image_name = data[-1]['image']
# date, image_name
# ('2026-09-20 22:36:17', 'epic_1b_20260920224105')
# d, t = date.split(' ')
# y, m, d = d.split('-')
# y, m, d
# ('2026', '09', '20')
# imageurl = f"https://epic.gsfc.nasa.gov/archive/natural/{y}/{m}/{d}/jpg/{image_name}.jpg"
# resp = urequests.get(imageurl)
# resp.status_code
# 200
# j.open_ram(resp.content)
# Traceback (most recent call last):
#   File "<stdin>", line 1, in <module>
# AttributeError: 'jpegdec' object has no attribute 'open_ram'
# j.open_RAM(resp.content)
# True
# j.decode(0, 0, jpegdec.JPEG_SCALE_HALF)
# Trueame}.jpg"
# presto.update()
# display.update()
# presto.update()
# True
# presto.update()
# with open(f"epic_images/{image_name}.jpg", "wb") as f:
# ...     f.write(resp.content)
