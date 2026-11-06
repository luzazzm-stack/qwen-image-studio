# App icon matching the UI (adapted from Wan Studio's): black rounded square, tiffany glow, white picture frame + sun + mountain.
from PIL import Image, ImageDraw, ImageFilter
S = 512


def icon():
    base = Image.new("RGBA", (S, S), (8, 8, 10, 255))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-120, -160, 330, 290), fill=(129, 216, 208, 120))
    base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(90)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=116, fill=255)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    im.paste(base, (0, 0), mask)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((110, 110, 402, 402), radius=64, outline="white", width=30)
    d.ellipse((290, 160, 350, 220), fill="white")
    d.polygon([(150, 362), (232, 256), (288, 322), (318, 290), (362, 362)], fill="white")
    return im


im = icon()
small = im.resize((256, 256), Image.LANCZOS)
small.save("web/qis.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
small.save("web/icon-256.png")
im.save("web/icon-512.png")
