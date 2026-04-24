import os

path = r'd:\hackathon\public\index.html'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = 'Implementation Narrative</div>'
replacement = '''Societal Impact</div>
                    <div id="impactSocietal"
                        style="font-size: 13px; color: #10B981; font-weight: 500; line-height: 1.4; margin-bottom: 12px;">
                    </div>
                    <div
                        style="font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">
                        Implementation Narrative</div>'''

if target in content:
    content = content.replace(target, replacement)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patch successful")
else:
    print("Target not found")
