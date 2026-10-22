"""Readable social cards without remote assets or rendering services."""
from PIL import Image, ImageDraw, ImageFont


def load_font(size, bold=True):
    suffix = '-Bold' if bold else ''
    for path in (
        f'/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf',
        f'/usr/share/fonts/truetype/liberation/LiberationSans{suffix or "-Regular"}.ttf',
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def wrap_pixels(draw, text, font, width):
    lines, line = [], ''
    for word in ' '.join(str(text or '').split()).split():
        candidate = f'{line} {word}'.strip()
        if draw.textlength(candidate, font=font) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
            line = ''
        for char in word:
            if line and draw.textlength(line + char, font=font) > width:
                lines.append(line)
                line = ''
            line += char
    if line:
        lines.append(line)
    return lines


def fit_text(draw, text, width, max_lines, sizes):
    for size in sizes:
        font = load_font(size)
        lines = wrap_pixels(draw, text, font, width)
        if len(lines) <= max_lines:
            return font, lines, size
    lines = lines[:max_lines]
    if lines:
        last = lines[-1]
        while last and draw.textlength(last + '…', font=font) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + '…'
    return font, lines, size


def render_job_card(job):
    image = Image.new('RGB', (1200, 630), '#f6f7f2')
    draw = ImageDraw.Draw(image)
    ink, green = '#17251e', '#16803c'
    draw.rectangle((0, 0, 15, 630), fill=green)
    draw.text((64, 42), 'RemoteImpact', font=load_font(32), fill=green)
    draw.text((64, 110), 'REMOTE OPPORTUNITY', font=load_font(22), fill='#526357')
    font, lines, size = fit_text(draw, job.title, 1060, 3, range(88, 53, -2))
    y = 164
    for line in lines:
        draw.text((60, y), line, font=font, fill=ink, anchor='lt')
        y += size + 12
    org = job.organization.name if job.organization else ''
    font, lines, _ = fit_text(draw, org, 1060, 1, range(40, 27, -2))
    draw.text((64, 467), lines[0] if lines else '', font=font, fill=green, anchor='lt')
    draw.line((64, 535, 1136, 535), fill='#d7ded4', width=2)
    draw.text((64, 560), 'View role & apply', font=load_font(26), fill=ink)
    draw.text((1136, 560), 'remoteimpact.org', font=load_font(24, bold=False), fill='#526357', anchor='rt')
    return image
