cd ~/sdr/leandvb-GUI && python3 - <<'EOF'
import ast
src = open("leandvb-GUI.py").read(); t = ast.parse(src)
fs = sorted(((n.end_lineno - n.lineno + 1, n.name, n.lineno) for n in ast.walk(t)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), reverse=True)
top = [n for n in t.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
print("Gesamt", len(src.splitlines()), "Zeilen,", len(fs), "Funktionen,",
      sum(n.end_lineno - n.lineno + 1 for n in top), "Zeilen in Top-Level-Funktionen")
for l, nm, ln in fs[:30]:
    print(f"{l:5d} {nm:40s} ab Zeile {ln}")
print("\nGroße Blöcke auf Modulebene (>50 Zeilen, z.B. _WEB_PAGE):")
for n in t.body:
    l = n.end_lineno - n.lineno + 1
    if l > 50 and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        print(f"{l:5d} {type(n).__name__} ab Zeile {n.lineno}")
EOF

