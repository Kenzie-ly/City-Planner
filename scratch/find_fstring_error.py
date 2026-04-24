import ast

def check_fstrings(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    # Check the expression part for backslashes
                    # We can't easily get the source of just the expression from the AST node directly without the source code
                    # but we can check if any string literal inside the expression has a backslash.
                    pass

def manual_check(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines):
        # Look for f"..." or f'...'
        # This is a bit complex with regex, so let's just look for f followed by quotes and then {
        if 'f"' in line or "f'" in line:
            if '{' in line:
                # Find the part between { and }
                import re
                matches = re.findall(r'\{(.*?)\}', line)
                for m in matches:
                    if '\\' in m:
                        print(f"Line {i+1}: Found backslash in f-string expression: {line.strip()}")

if __name__ == "__main__":
    manual_check(r'd:\hackathon\backend\app.py')
