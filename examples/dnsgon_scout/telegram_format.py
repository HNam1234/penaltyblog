"""Render the small Markdown subset used by AI replies as native Telegram entities."""
import re
from urllib.parse import urlsplit


def units(text):
    return len(text.encode('utf-16-le')) // 2


def safe_url(value):
    try:
        url = urlsplit(value)
        return (len(value) <= 2048 and url.scheme in {'http', 'https'} and bool(url.hostname)
                and not url.username and not url.password
                and not re.search(r'[\s\x00-\x1f\x7f]', value))
    except ValueError:
        return False


def inline(source, depth=0):
    text, entities, pos, length = [], [], 0, 0
    def add(value, kind=None, extra=None):
        nonlocal length
        start = length; text.append(value); length += units(value)
        if kind and value:
            entities.append({'type': kind, 'offset': start, 'length': units(value), **(extra or {})})
    while pos < len(source):
        # Keep code literal, including Markdown-looking text and citation examples.
        if source[pos] == '`':
            marker = re.match(r'`+', source[pos:])[0]
            end = source.find(marker, pos + len(marker))
            if end >= 0:
                add(source[pos+len(marker):end], 'code'); pos = end+len(marker); continue
        if source[pos] == '[' and depth < 8:
            close = source.find('](', pos+1)
            if close >= 0:
                end, level = close+2, 1
                while end < len(source) and level:
                    if source[end] == '(': level += 1
                    elif source[end] == ')': level -= 1
                    end += 1
                url = source[close+2:end-1]
                if level == 0 and safe_url(url):
                    label, children = inline(source[pos+1:close], depth+1)
                    start = length; add(label, 'text_link', {'url': url})
                    entities.extend({**e, 'offset': start+e['offset']} for e in children if e['type'] not in {'code', 'pre'})
                    pos = end; continue
        matched = False
        for marker, kind in [('**', 'bold'), ('__', 'bold'), ('~~', 'strikethrough')]:
            if source.startswith(marker, pos) and depth < 8:
                end = source.find(marker, pos+len(marker))
                if end > pos+len(marker):
                    value, children = inline(source[pos+len(marker):end], depth+1)
                    start = length; add(value, None if any(e['type'] in {'code', 'pre'} for e in children) else kind)
                    entities.extend({**e, 'offset': start+e['offset']} for e in children)
                    pos = end+len(marker); matched = True; break
        if matched: continue
        if source[pos] == '\\' and pos+1 < len(source) and source[pos+1] in r'\`*_{}[]()#+-.!':
            pos += 1
        add(source[pos]); pos += 1
    return ''.join(text), entities


def render(source):
    chunks, entities, length = [], [], 0
    # Process prose and fenced code separately so formatting never corrupts code.
    for block in re.split(r'(```[^\n`]*\n[\s\S]*?\n?```)', source.replace('\r\n', '\n')):
        if not block: continue
        if block.startswith('```') and block.endswith('```'):
            header, _, body = block[3:-3].partition('\n')
            value = body.rstrip('\n')
            extra = {'language': header.strip()} if re.fullmatch(r'[\w+#.-]{1,24}', header.strip()) else {}
            current = [{'type': 'pre', 'offset': 0, 'length': units(value), **extra}] if value else []
        else:
            block = re.sub(r'\ue200cite\ue202[^\ue201]*\ue201', '', block)
            block = re.sub(r'(?m)[ \t]+$', '', block)
            block = re.sub(r'(?m)^#{1,6}\s+(.+?)\s*#*$', r'**\1**', block)
            block = re.sub(r'(?m)^[-*]\s+', '• ', block)
            block = re.sub(r'(?<!\n)\n(?=(?:• |\d+[.)] ))', '\n\n', block)
            block = re.sub(r'\n{3,}', '\n\n', block)
            value, current = inline(block)
        chunks.append(value)
        entities.extend({**e, 'offset': length+e['offset']} for e in current)
        length += units(value)
    text = ''.join(chunks)
    footer = re.search(r'(?:^|\n\n)(Luna · Extra high|Sol · Medium|Gemini · gemini-[\w.-]+|Hai nguồn AI · Codex \+ Gemini)$', text)
    if footer:
        entities.append({'type':'italic','offset':units(text[:footer.start(1)]),'length':units(footer[1])})
    return text, sorted(entities, key=lambda e: (e['offset'], -e['length']))


def messages(source, limit=3600):
    text, entities = render(source)
    # Map Python character boundaries to Telegram's UTF-16 offsets, including emoji.
    offsets = [0]
    for char in text: offsets.append(offsets[-1] + units(char))
    start = 0
    while start < len(text):
        end = start
        while end < len(text) and offsets[end+1]-offsets[start] <= limit: end += 1
        overlapping = [e for e in entities if e['offset'] < offsets[end] and e['offset']+e['length'] > offsets[start]]
        if len(overlapping) > 90:
            boundary = overlapping[90]['offset']
            while end > start and offsets[end] > boundary: end -= 1
        if end < len(text):
            for separator in ('\n\n', '\n', ' '):
                split = text.rfind(separator, start+(end-start)//2, end)
                if split >= 0:
                    end = split+len(separator); break
        if end == start: end = start+1
        first, last = offsets[start], offsets[end]
        packet = {'text': text[start:end]}
        clipped = []
        for entity in entities:
            a, b = max(first, entity['offset']), min(last, entity['offset']+entity['length'])
            if b > a: clipped.append({**entity, 'offset': a-first, 'length': b-a})
        if packet['text'].strip():
            if clipped: packet['entities'] = clipped
            yield packet
        start = end
