import asyncio
from life.life import Life

### Go!
if __name__ == "__main__":
    life = Life()
    # life.setup(kind="rle", filename="blinkers")
    life.setup(kind="kaleidosoup")

    asyncio.run(life._app_loop())
