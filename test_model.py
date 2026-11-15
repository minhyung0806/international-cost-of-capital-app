"""Pure calculation smoke test, without starting Streamlit."""
import ast
from pathlib import Path
p=Path(__file__).with_name('app.py')
tree=ast.parse(p.read_text())
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='calc')
ns={}
import numpy as np
ns['np']=np
exec(compile(ast.Module(body=[fn],type_ignores=[]),str(p),'exec'),ns)
r=ns['calc'](.8,35,30,6.5,4,12,1.5)
for key,target in {'Levered beta':1.022857142857,'USD equity (%)':10.64857142857,'Foreign equity (%)':19.1600,'USD WACC (%)':8.5265,'Foreign WACC (%)':16.0670}.items():
    assert abs(r[key]-target)<0.00015,(key,r[key],target)
print('Colombia lecture example: PASS')
