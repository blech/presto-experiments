import math
import time
from random import randint, randrange

from presto import Presto


class Icosahedron(object):
    # The corners of the icosahedron
    vertices = [
        [0.0, -1.0, -1.618033988749895],
        [-1.0, -1.618033988749895, 0.0],
        [-1.618033988749895, 0.0, -1.0],
        [0.0, -1.0, 1.618033988749895],
        [-1.0, 1.618033988749895, 0.0],
        [1.618033988749895, 0.0, -1.0],
        [0.0, 1.0, -1.618033988749895],
        [1.0, -1.618033988749895, 0.0],
        [-1.618033988749895, 0.0, 1.0],
        [0.0, 1.0, 1.618033988749895],
        [1.0, 1.618033988749895, 0.0],
        [1.618033988749895, 0.0, 1.0],
    ]

    face_vertices = [
        [0, 1, 2],
        [0, 2, 6],
        [0, 5, 6],
        [0, 5, 7],
        [0, 1, 7],
        [7, 1, 3],
        [3, 11, 7],
        [11, 5, 7],
        [5, 10, 11],
        [9, 10, 11],
        [4, 9, 10],
        [4, 6, 10],
        [4, 2, 6],
        [5, 6, 10],
        [11, 9, 3],
        [8, 9, 3],
        [8, 1, 2],
        [8, 1, 3],
        [4, 8, 9],
        [4, 8, 2],
    ]

    def __init__(self, fov, distance, x, y, speed):
        self.tick = time.ticks_ms() / 1000.0
        self.cos = math.cos(self.tick)
        self.sin = math.sin(self.tick)
        self.fov = fov
        self.distance = distance
        self.pos_x = x
        self.pos_y = y
        self.speed = speed

        self.icosahedron_points = []

    # Project our points
    def to_2d(self, x, y, z, pos_x, pos_y, fov, distance):
        factor = fov / (distance + z)
        x = x * factor + pos_x
        y = -y * factor + pos_y

        return int(x), int(y), z

    def return_tick(self):
        return self.tick

    # Clear our points and recalculate the sin and cos values
    def _update(self):

        self.icosahedron_points = []

        self.tick = time.ticks_ms() / (self.speed * 1000)
        self.cos = math.cos(self.tick)
        self.sin = math.sin(self.tick)

    def set_fov(self, fov):
        self.fov = fov

    def set_distance(self, distance):
        self.distance = distance

    def set_speed(self, speed):
        self.speed = speed

    def set_x(self, x):
        self.pos_x = x

    def set_y(self, y):
        self.pos_y = y

    def get_fov(self):
        return self.fov

    # Rotate on XYZ and save the new points in our list
    def rotate(self):

        for v in self.vertices:

            start_x, start_y, start_z = v

            # X
            y = start_y * self.cos - start_z * self.sin
            z = start_y * self.sin + start_z * self.cos

            # Y
            x = start_x * self.cos - z * self.sin
            z = start_x * self.sin + z * self.cos

            # Z
            n_y = x * self.sin + y * self.cos
            n_x = x * self.cos - y * self.sin

            y = n_y
            x = n_x

            point = self.to_2d(x, y, z, self.pos_x, self.pos_y, self.fov, self.distance)
            self.icosahedron_points.append(point)

    # Draw the vertices of the icosahedron so we can see it on screen!
    def draw_vertices(self, display):
        for idx, point in enumerate(self.icosahedron_points):
            display.set_pen(display.create_pen(255, 255, 255))
            # display.circle(point[0], point[1], 2)
            display.text(str(idx), point[0], point[1], 320, 1)

    def draw_edges(self, display):
        for edge in self.edges:
            p0 = self.icosahedron_points[edge[0]]
            p1 = self.icosahedron_points[edge[1]]
            display.set_pen(display.create_pen(0, 0, 0))
            display.line(p0[0], p0[1], p1[0], p1[1])

    def draw_faces(self, display):
        idx_order = {}
        for idx, fv in enumerate(self.face_vertices):
            p0 = self.icosahedron_points[fv[0]]
            p1 = self.icosahedron_points[fv[1]]
            p2 = self.icosahedron_points[fv[2]]
            z_avg = (p0[2] + p1[2] + p2[2])/3
            idx_order[idx] = z_avg

        draw_order = sorted(idx_order.items(), key=lambda kv: kv[1])

        for idx, za in draw_order:
            fv = self.face_vertices[idx]
            p0 = self.icosahedron_points[fv[0]]
            p1 = self.icosahedron_points[fv[1]]
            p2 = self.icosahedron_points[fv[2]]

            display.set_pen(display.create_pen_hsv(idx/20, 1.0, 1.0))
            display.triangle(
                p0[0], p0[1],
                p1[0], p1[1],
                p2[0], p2[1],
            )
            display.set_pen(display.create_pen(0, 0, 0))
            for fe in ((p0, p1), (p1, p2), (p2, p0)):
                display.line(fe[0][0], fe[0][1], fe[1][0], fe[1][1])


def main():
    # Setup for the Presto display
    presto = Presto()
    display = presto.display
    WIDTH, HEIGHT = display.get_bounds()

    BLACK = display.create_pen(0, 0, 0)
    WHITE = display.create_pen(255, 255, 255)
    GREY = display.create_pen(153, 153, 153)

    # Setup the first 3 objects.
    icosahedrons = [
        Icosahedron(16, 4, WIDTH / 2, HEIGHT / 2, 3.0),
#        Icosahedron(32, 8, 100, 100, 0.9),
#        Icosahedron(32, 12, 200, 200, 0.5),
    ]

    # Set our initial pen colour
    pen = display.create_pen_hsv(1.0, 1.0, 1.0)

    while True:

        # We'll use this for cycling through the rainbow
        t = time.ticks_ms() / 1000

        # Set the layer we're going to be drawing to.
        display.set_layer(0)

        # Clear the screen and set the pen colour for the icosahedrons
        display.set_pen(BLACK)
        display.clear()
        display.set_pen(GREY)

        # show text & rainbow colours (skipped)
        # display.text("Flying icosahedrons!", 90, 110, 320, 1)
        # display.reset_pen(pen)
        # pen = display.create_pen_hsv(t, 1.0, 1.0)
        # display.set_pen(pen)

        # Now we go through each Cube object we have in 'icosahedrons'
        # and increase the FOV angle so it appears closer to the screen.
        # We'll also rotate the cube during this loop too.
        for i, icosahedron in enumerate(icosahedrons):
            fov = icosahedron.get_fov()
            fov = 128
            icosahedron.set_fov(fov)
            icosahedron.rotate()
            icosahedron.draw_faces(display)
            icosahedron.draw_vertices(display)
            icosahedron._update()

            # We want the cubes to disappear randomly as they appear close to the screen, so we'll decide when this happens based on the current FOV
            # We'll replace that cube with a new one and start the process from the beginning!
            if fov > randint(250, 600):
                icosahedrons[i] = Icosahedron(8, 4, randint(10, WIDTH), randint(10, HEIGHT), randrange(4, 9) / 10)

        # Finally we update the screen with our changes :)
        presto.update()
        time.sleep(0.1)

if __name__ == "__main__":
    main()
