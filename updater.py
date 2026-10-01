import os, sys, re, json, hashlib, shutil, subprocess
from pathlib import Path
from urllib.request import Request, urlopen

APP_VERSION='2.4.1'
UPDATE_REPO='combatengineer88/Media-library-database'
UPDATE_API=f'https://api.github.com/repos/{UPDATE_REPO}/releases/latest'

def version_tuple(v):
    nums=re.findall(r'\d+',str(v)); nums=(nums+['0','0','0'])[:3]
    return tuple(map(int,nums))

def latest_release():
    req=Request(UPDATE_API,headers={'User-Agent':f'MediaLibraryDatabase/{APP_VERSION}','Accept':'application/vnd.github+json'})
    with urlopen(req,timeout=12) as r: return json.load(r)

def find_update_asset(release):
    assets=release.get('assets',[])
    p=[a for a in assets if a.get('name','').lower()=='media-library-database-update.zip']
    if not p: p=[a for a in assets if a.get('name','').lower().endswith('.zip') and 'update' in a.get('name','').lower()]
    return p[0] if p else None

def download_and_stage(asset, app_data_dir, script_path):
    update_dir=Path(app_data_dir)/'updates'; update_dir.mkdir(parents=True,exist_ok=True)
    zip_path=update_dir/'Media-Library-Database-update.zip'
    req=Request(asset['browser_download_url'],headers={'User-Agent':f'MediaLibraryDatabase/{APP_VERSION}','Accept':'application/octet-stream'})
    with urlopen(req,timeout=120) as src, open(zip_path,'wb') as dst: shutil.copyfileobj(src,dst)
    digest=asset.get('digest') or ''
    if digest.startswith('sha256:'):
        expected=digest.split(':',1)[1].lower(); got=hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
        if got != expected: raise RuntimeError('Update checksum verification failed.')
    install_dir=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(script_path).resolve().parent
    restart=(f'"{sys.executable}"' if getattr(sys,'frozen',False) else f'"{sys.executable}" "{Path(script_path).resolve()}"')
    expanded=update_dir/'expanded'; bat=update_dir/'apply_update.bat'
    zp=str(zip_path).replace("'","''"); ex=str(expanded).replace("'","''")
    lines=['@echo off','setlocal',f'set PID={os.getpid()}',':waitloop','tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL','if not errorlevel 1 (timeout /t 1 /nobreak >NUL & goto waitloop)',f'powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath \'{zp}\' -DestinationPath \'{ex}\' -Force"',f'xcopy /E /Y /I "{expanded}\\*" "{install_dir}\\" >NUL',f'rmdir /S /Q "{expanded}"',f'del /Q "{zip_path}"',f'start "" {restart}','del "%~f0"']
    bat.write_text('\r\n'.join(lines)+'\r\n',encoding='utf-8')
    subprocess.Popen(['cmd','/c','start','',str(bat)],creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
