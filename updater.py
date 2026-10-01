import os, sys, re, json, hashlib, shutil, subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

APP_VERSION='2.4.5'
UPDATE_REPO='combatengineer88/Media-library-database'
UPDATE_API=f'https://api.github.com/repos/{UPDATE_REPO}/releases/latest'

def version_tuple(v):
    nums=re.findall(r'\d+',str(v)); nums=(nums+['0','0','0'])[:3]
    return tuple(map(int,nums))

class NoPublishedRelease(Exception):
    pass

def latest_release():
    req=Request(UPDATE_API,headers={'User-Agent':f'MediaLibraryDatabase/{APP_VERSION}','Accept':'application/vnd.github+json'})
    try:
        with urlopen(req,timeout=12) as r:
            return json.load(r)
    except HTTPError as e:
        if e.code == 404:
            raise NoPublishedRelease('No published releases are available yet.') from e
        raise RuntimeError(f'GitHub returned HTTP {e.code} while checking for updates.') from e
    except URLError as e:
        raise RuntimeError(f'Could not connect to GitHub: {e.reason}') from e

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
    if getattr(sys,'frozen',False):
        restart_exe=Path(sys.executable)
        restart_args=''
    else:
        restart_exe=Path(sys.executable)
        restart_args=str(Path(script_path).resolve())

    ps1=update_dir/'apply_update.ps1'
    ps = r'''param(
  [int]$OldPid,
  [string]$ZipPath,
  [string]$InstallDir,
  [string]$RestartExe,
  [string]$RestartArgs
)
$ErrorActionPreference = "Stop"
try {
  while (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 500 }
  $expanded = Join-Path (Split-Path $ZipPath) "expanded"
  if (Test-Path $expanded) { Remove-Item $expanded -Recurse -Force }
  Expand-Archive -LiteralPath $ZipPath -DestinationPath $expanded -Force
  Copy-Item -Path (Join-Path $expanded "*") -Destination $InstallDir -Recurse -Force
  $installedExe = Join-Path $InstallDir "MediaLibraryDatabase.exe"
  if (-not (Test-Path $installedExe)) { throw "Updated executable was not copied to the installation folder." }
  Remove-Item $expanded -Recurse -Force
  Remove-Item $ZipPath -Force
  if ([string]::IsNullOrWhiteSpace($RestartArgs)) {
    Start-Process -FilePath $RestartExe -WorkingDirectory $InstallDir
  } else {
    Start-Process -FilePath $RestartExe -ArgumentList @($RestartArgs) -WorkingDirectory $InstallDir
  }
} catch {
  $msg = $_.Exception.Message
  Add-Type -AssemblyName PresentationFramework
  [System.Windows.MessageBox]::Show("Media Library Database update failed.\n\n$msg","Update failed") | Out-Null
}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
'''
    ps1.write_text(ps,encoding='utf-8')
    args=['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ps1),
          '-OldPid',str(os.getpid()),'-ZipPath',str(zip_path),'-InstallDir',str(install_dir),
          '-RestartExe',str(restart_exe),'-RestartArgs',restart_args]
    subprocess.Popen(args,creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
