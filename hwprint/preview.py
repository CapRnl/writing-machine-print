"""把排好的页面画成图片（预览用）。"""

from PIL import Image, ImageDraw


def render_page(placed, page, page_w, page_h, px_per_mm=6.0, ruled=None, header_labels=None,
                bg=(255, 255, 255), ink=(20, 20, 20), line_color=(150, 150, 150), ss=3, pen_mm=0.35):
    """ruled: [(x0, x1, y), ...] 画格线；header_labels: [(x, y_base, text), ...] 用系统字体画印刷好的栏目名。"""
    k = px_per_mm * ss
    W, H = int(page_w * k) + 2, int(page_h * k) + 2
    im = Image.new('RGB', (W, H), bg)
    d = ImageDraw.Draw(im)
    if ruled:
        for x0, x1, y in ruled:
            d.line([(x0 * k, y * k), (x1 * k, y * k)], fill=line_color, width=max(1, int(0.25 * k)))
    if header_labels:
        from PIL import ImageFont
        try:
            f = ImageFont.truetype('msyh.ttc', int(4.2 * k))
        except Exception:
            f = ImageFont.load_default()
        for x, y, t in header_labels:
            d.text((x * k, y * k), t, fill=(120, 120, 120), font=f, anchor='ls')
    wpx = max(1, int(round(pen_mm * k)))
    for p in placed:
        if p.page != page:
            continue
        for s in p.strokes:
            if len(s) == 1:
                x, y = s[0]
                d.ellipse([x * k - wpx / 2, y * k - wpx / 2, x * k + wpx / 2, y * k + wpx / 2], fill=ink)
            else:
                d.line([(x * k, y * k) for x, y in s], fill=ink, width=wpx, joint='curve')
    return im.resize((W // ss, H // ss), Image.LANCZOS)
