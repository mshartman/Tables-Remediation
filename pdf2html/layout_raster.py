"""Recognize page-sized raster composites without swallowing their text layer."""


def union_area(boxes):
    xs=sorted({x for box in boxes for x in (box[0],box[2])})
    area=0
    for left,right in zip(xs,xs[1:]):
        intervals=sorted((b[1],b[3]) for b in boxes if b[0]<right and b[2]>left)
        end=None;length=0
        for low,high in intervals:
            if end is None or low>end:length+=high-low;end=high
            elif high>end:length+=high-end;end=high
        area+=(right-left)*length
    return area


def covers(outer,inner):
    return outer[0]<=inner[0]+1 and outer[1]<=inner[1]+1 and outer[2]>=inner[2]-1 and outer[3]>=inner[3]-1


def mark_hidden_visual_spans(page, lines):
    """Keep hidden OCR semantic, but avoid repainting it over the scan."""
    image_ops = [(i, b) for i, (kind, b) in enumerate(page.get_bboxlog()) if kind == 'fill-image']
    glyphs = {}
    def key(code, origin):
        return code, round(origin[0], 2), round(origin[1], 2)
    for span in page.get_texttrace():
        hidden = (span['type'] == 3 or span.get('opacity', 1) < .01 or any(
            seq > span['seqno'] and covers(box, span['bbox']) for seq, box in image_ops))
        for code, _, origin, _ in span['chars']:
            glyphs.setdefault(key(code, origin), set()).add(hidden)
    count = 0
    for line in lines:
        for span in line['spans']:
            chars = [ch for ch in span.get('chars', []) if ch['c'].strip()]
            # Unknown or ambiguous glyph mappings stay visible, rather than lose text.
            if chars and all(len(ch['c']) == 1 and glyphs.get(key(ord(ch['c']), ch['origin'])) == {True} for ch in chars):
                span['visual_hidden'] = True
                count += 1
    return count


def page_image_text_layer(page):
    if len(page.get_text().strip())<200:return None
    width,height=page.rect.width,page.rect.height
    boxes=[(max(0,i['bbox'][0]),max(0,i['bbox'][1]),min(width,i['bbox'][2]),min(height,i['bbox'][3]))
           for i in page.get_image_info()]
    boxes=[b for b in boxes if b[2]>b[0] and b[3]>b[1]]
    coverage=union_area(boxes)/(width*height)
    if coverage<.5:return None
    # Some OCR PDFs paint text normally, then cover it with the scan image.
    image_ops=[(i,b) for i,(kind,b) in enumerate(page.get_bboxlog()) if kind=='fill-image']
    total=hidden=0
    for span in page.get_texttrace():
        dx,dy=span.get('dir',(1,0))
        # Only horizontal text gets a reconstructed visual copy; margin notes do not.
        if abs(dx-1)>.01 or abs(dy)>.01:continue
        count=len(span['chars']);total+=count
        if span['type']==3 or span.get('opacity',1)<.01 or any(
            seq>span['seqno'] and covers(box,span['bbox']) for seq,box in image_ops):hidden+=count
    # An inset scan can leave wide white borders; hidden OCR supplies extra evidence.
    if coverage<.92 and not (total>=200 and hidden>=total*.95):return None
    return {'image_area_fraction':round(coverage,4),'raster_parts':len(boxes),
            'recognition':'near_full_raster' if coverage>=.92 else 'inset_raster_with_hidden_text',
            'hide_visual_text':bool(total and total==hidden),
            'review':'Text layer is unverified; figure segmentation and image alternatives require source review.'}
