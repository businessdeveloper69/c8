import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

WINDOWS = os.name == 'nt'
parent = Path(__file__).resolve().parent
lane, source, consumer, scratch = sys.argv[1:]
source, consumer, scratch = (Path(p).resolve() for p in (source, consumer, scratch))
scratch.mkdir(exist_ok=True, parents=True)
cli = source / 'bin/c8.js'
assert cli.exists()
results = {"lane": lane, "platform": sys.platform, "c8_sha": subprocess.check_output(["git","rev-parse","HEAD"], cwd=source, text=True).strip(), "cases": {}}


def prepare_cli(cwd):
    shim = cwd/'node_modules/.bin'
    shim.mkdir(parents=True,exist_ok=True)
    if WINDOWS:
        (shim/'c8.cmd').write_text('@echo off\r\nnode "' + str(cli) + '" %*\r\n')
    else:
        from shlex import quote
        (shim/'c8').write_text('#!/bin/sh\nexec node ' + quote(str(cli)) + ' "$@"\n')
        (shim/'c8').chmod(0o755)


def run(name, cwd, script):
    trace = scratch / (name+'.argv')
    if trace.exists():
        trace.unlink()
    env = {**os.environ, 'C8_488_BIN': str(cli), 'C8_488_TRACE': str(trace), 'NODE_OPTIONS': '--require="' + (parent/'observe.cjs').as_posix() + '"'}
    cmd = [os.environ['ComSpec'], '/d', '/s', '/c', 'npm.cmd run '+script] if WINDOWS else ['npm', 'run', script]
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    (scratch/(name+'.log')).write_text(p.stdout, encoding='utf-8')
    argv = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()] if trace.is_file() else []
    print(lane, name, p.returncode, p.stdout[-1100:], flush=True)
    item = results['cases'][name] = {'exit': p.returncode, 'argv': argv}
    return item


def actual():
    cwd=consumer/'packages/common'
    script = json.loads((cwd/'package.json').read_text())['scripts']['test']
    assert "--exclude 'dist/test/**/*.js'" in script
    prepare_cli(cwd)
    report=cwd/'reports/coverage'
    if report.exists():
        shutil.rmtree(report)
    item=run('stryker',cwd,'test')
    assert (report/'index.html').is_file()
    page=(report/'index.html').read_text(encoding='utf-8')
    percentages = re.findall(r'<span class="strong">\s*([^<]+?)\s*</span>\s*<span class="quiet">\s*(Statements|Branches|Functions|Lines)\s*</span>',page,re.I)
    item['thresholds']={t:html.unescape(v).strip() for v,t in percentages}
    item['covered_files']=sorted(p.relative_to(report).as_posix()[:-5] for p in report.rglob('*.html') if p.name != 'index.html')
    output=(scratch/'stryker.log').read_text(encoding='utf-8')
    item['passed_test_count']=int(re.search(r'(\d+) passing',output).group(1))
    item['command']=script
    print('ACTUAL',json.dumps(item),flush=True)
    observed=next(a for a in item['argv'] if a['bin']=='c8')['argv']
    pattern=observed[observed.index('--exclude')+1]
    assert pattern==("'dist/test/**/*.js'" if WINDOWS else "dist/test/**/*.js"),pattern
    assert item['passed_test_count']>0 and item['covered_files'] and len(item['thresholds'])==4
    tests_included=any(re.search(r'(^|/)test/',n) for n in item['covered_files'])
    broken=WINDOWS and lane=='base'
    assert tests_included==broken,(tests_included,broken,item)
    assert (item['exit']!=0)==broken,(item['exit'], broken, item)


def controls():
    cwd=scratch/'small-package'
    cwd.mkdir(exist_ok=True)
    prepare_cli(cwd)
    (cwd/'skip').mkdir(exist_ok=True)
    (cwd/'skip/ignored.cjs').write_text("module.exports = function uncovered(x) { return x ? 1 : 2 };\n")
    (cwd/"it's").mkdir(exist_ok=True)
    (cwd/"it's/ignored.cjs").write_text("module.exports = () => 2;\n")
    (cwd/'-dash').mkdir(exist_ok=True)
    (cwd/'-dash/ignored.cjs').write_text("module.exports = () => 3;\n")
    (cwd/'child.cjs').write_text("require('./skip/ignored.cjs'); require(\"./it's/ignored.cjs\"); require('./-dash/ignored.cjs');\n")
    (cwd/'config.json').write_text(json.dumps({'exclude':["'skip/**'"]}))
    tail="node child.cjs --exclude 'child/**' -x fake -- 'tail/**'"
    prefix='c8 --reporter=json-summary --reports-dir=report '
    scripts={'raw':tail,'cli':prefix+"--exclude 'skip/**' "+tail,'alias':prefix+"-x 'skip/**' "+tail,'double':prefix+'--exclude "skip/**" '+tail,'configuration':prefix+'--config config.json '+tail}
    scripts["apostrophe"] = prefix + ("--exclude 'it's/**' " if WINDOWS else "--exclude \"it's/**\" ") + tail
    scripts["dashboard-alias-equals"] = prefix + "-x='skip/**' " + tail
    scripts["dash"] = prefix + "--exclude '-dash/**' " + tail
    (cwd/'package.json').write_text(json.dumps({'private':True,'scripts':scripts}))
    argvc=None
    for key in scripts:
        item=run(key,cwd,key)
        assert item['exit']==0
        child=next(s for s in item['argv'] if s['bin']=='child.cjs')['argv']
        if key=='raw':
            argvc=child
            continue
        assert child==argvc,(key,child,argvc)
        table=json.loads((cwd/'report/coverage-summary.json').read_text())
        files=sorted(os.path.relpath(n,cwd).replace('\\','/') for n in table if n!='total')
        item.update({'files':files,'lines':table['total']['lines']})
        if key in ('cli','alias','double','configuration'):
            expected_included=key=='configuration' or (WINDOWS and lane=='base' and key in ('cli','alias','dashboard-alias-equals'))
            assert ('skip/ignored.cjs' in files)==expected_included,(lane,key,files,expected_included)
        elif key=='dashboard-alias-equals':
            if lane!='base' or not WINDOWS:
                assert 'skip/ignored.cjs' not in files,(lane,key,files)
        elif key=='apostrophe':
            assert ("it's/ignored.cjs" in files)==(WINDOWS and lane=='base'),(lane,key,files)
        elif key=='dash':
            assert ('-dash/ignored.cjs' in files)==(not WINDOWS or lane=='base'),(lane,key,files)


try:
    actual()
    controls()
finally:
    (scratch/'receipt.json').write_text(json.dumps(results,indent=2), encoding='utf-8')
    print('C8_488_RECEIPT='+json.dumps(results),flush=True)
