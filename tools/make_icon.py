"""Génère assets/autowash.ico : goutte d'eau blanche sur fond bleu dégradé (16 à 256 px)."""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024  # dessin en grand puis réduction, pour des bords lisses
OUT = Path(__file__).resolve().parent.parent / "assets" / "autowash.ico"


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (SIZE, SIZE))
    top, bottom = (37, 99, 235), (6, 182, 212)  # bleu vers cyan
    for y in range(SIZE):
        t = y / (SIZE - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
        ImageDraw.Draw(gradient).line([(0, y), (SIZE, y)], fill=color)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=SIZE // 4.5, fill=255)
    img.paste(gradient, (0, 0), mask)

    d = ImageDraw.Draw(img)
    cx, r = SIZE / 2, SIZE * 0.24
    cy = SIZE * 0.6
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="white")  # bas de la goutte
    d.polygon([(cx, SIZE * 0.15), (cx - r * 0.94, cy - r * 0.34), (cx + r * 0.94, cy - r * 0.34)], fill="white")
    # reflet dans la goutte
    d.arc([cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62], start=110, end=190,
          fill=(37, 99, 235), width=int(SIZE * 0.035))
    # étincelle « propre » en haut à droite
    sx, sy, s = SIZE * 0.77, SIZE * 0.25, SIZE * 0.085
    d.polygon([(sx, sy - s), (sx + s * 0.25, sy - s * 0.25), (sx + s, sy), (sx + s * 0.25, sy + s * 0.25),
               (sx, sy + s), (sx - s * 0.25, sy + s * 0.25), (sx - s, sy), (sx - s * 0.25, sy - s * 0.25)],
              fill="white")
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    draw().resize((256, 256), Image.LANCZOS).save(OUT, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(f"Icône : {OUT}")
