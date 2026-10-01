import sys, os, sqlite3, hashlib, subprocess, json, shutil, re, csv
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from updater import APP_VERSION, NoPublishedRelease, version_tuple, latest_release, find_update_asset, download_and_stage

from PySide6.QtCore import Qt, QThread, Signal, QSize, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap, QIcon
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLineEdit,
    QFileDialog,QTableWidget,QTableWidgetItem,QHeaderView,QLabel,QComboBox,QMessageBox,QProgressBar,
    QListWidget,QSplitter,QAbstractItemView,QFormLayout,QDialog,QDialogButtonBox,QStackedWidget,
    QScrollArea,QGridLayout,QFrame,QTabWidget,QCheckBox,QInputDialog,QTreeWidget,QTreeWidgetItem)

APP_DIR = Path(os.getenv('LOCALAPPDATA', Path.home())) / 'MediaLibraryDB'
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / 'media_library.db'
THUMB_DIR = APP_DIR / 'thumbnails'; THUMB_DIR.mkdir(exist_ok=True)
POSTER_DIR = APP_DIR / 'posters'; POSTER_DIR.mkdir(exist_ok=True)
SETTINGS_PATH = APP_DIR / 'settings.json'

MEDIA_EXTS = {
    'Movies': {'.mp4','.mkv','.avi','.mov','.wmv','.m4v','.mpg','.mpeg','.webm','.ts','.mts','.m2ts','.flv'},
    'Music': {'.mp3','.flac','.wav','.m4a','.aac','.ogg','.wma','.opus'},
    'Photos': {'.jpg','.jpeg','.png','.gif','.bmp','.tif','.tiff','.webp','.heic'},
}
VIDEO_EXTS=MEDIA_EXTS['Movies']; AUDIO_EXTS=MEDIA_EXTS['Music']; PHOTO_EXTS=MEDIA_EXTS['Photos']

def load_settings():
    try: return json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
    except: return {}
def save_settings(d): SETTINGS_PATH.write_text(json.dumps(d,indent=2),encoding='utf-8')

def media_type(path):
    ext=Path(path).suffix.lower()
    if ext in VIDEO_EXTS: return 'Video'
    if ext in AUDIO_EXTS: return 'Audio'
    if ext in PHOTO_EXTS: return 'Photo'
    return None

def human_size(n):
    n=float(n or 0)
    for u in ['B','KB','MB','GB','TB']:
        if n<1024 or u=='TB': return f'{n:.1f} {u}'
        n/=1024

def fmt_duration(seconds):
    if not seconds: return ''
    try:
        s=int(float(seconds)); h=s//3600; m=(s%3600)//60; sec=s%60
        return f'{h}:{m:02d}:{sec:02d}' if h else f'{m}:{sec:02d}'
    except: return ''

def clean_title(name):
    s=Path(name).stem
    s=re.sub(r'\b(19|20)\d{2}\b.*$','',s)
    s=re.sub(r'(?i)\b(2160p|1080p|720p|480p|bluray|blu-ray|webrip|web-dl|hdrip|dvdrip|x264|x265|h264|h265|hevc|aac|dts)\b.*$','',s)
    s=re.sub(r'[._]+',' ',s); return re.sub(r'\s+',' ',s).strip(' -') or Path(name).stem

def infer_fields(path, typ, tags=None):
    tags=tags or {}; P=Path(path); name=P.name; stem=P.stem
    title=tags.get('title') or clean_title(name); year=None; series=''; season=None; episode=None
    artist=tags.get('artist',''); album=tags.get('album',''); genre=tags.get('genre','')
    m=re.search(r'\b((?:19|20)\d{2})\b',stem)
    if m: year=int(m.group(1))
    if typ=='Video':
        # Common episode styles: S01E02, S01.E02, S01 E02, 1x02, S01E02E03.
        patterns=[r'(?i)(?<![A-Za-z0-9])S(?:eason[ ._-]*)?(\d{1,2})[ ._-]*E(?:p(?:isode)?[ ._-]*)?(\d{1,3})(?!\d)',
                  r'(?i)(?<!\d)(\d{1,2})[ ._-]*x[ ._-]*(\d{1,3})(?!\d)']
        ep=None
        for pat in patterns:
            ep=re.search(pat,stem)
            if ep: break
        parts=list(P.parts)
        # Folder hints allow layouts such as TV Shows/Series Name/Season 2/Episode 03.mkv.
        season_folder=None; season_idx=None
        for idx,part in enumerate(parts[:-1]):
            sm=re.fullmatch(r'(?i)(?:season|series)[ ._-]*(\d{1,2})',part.strip())
            if sm: season_folder=int(sm.group(1)); season_idx=idx
        tv_root_words={'tv','tv shows','television','shows','series'}
        tv_root_idx=next((i for i,x in enumerate(parts[:-1]) if x.lower().strip() in tv_root_words),None)
        if ep:
            season=int(ep.group(1)); episode=int(ep.group(2))
            prefix=clean_title(stem[:ep.start()])
            if prefix and prefix.lower() not in {'episode','ep'}: series=prefix
            elif season_idx is not None and season_idx>0: series=parts[season_idx-1]
            elif tv_root_idx is not None and tv_root_idx+1<len(parts)-1: series=parts[tv_root_idx+1]
            else: series=P.parent.name
            suffix=stem[ep.end():].strip(' ._-')
            title=clean_title(suffix) if suffix else f'Episode {episode}'
        elif season_folder is not None:
            season=season_folder
            em=re.search(r'(?i)(?:^|[ ._-])(?:E|EP|Episode)[ ._-]*(\d{1,3})(?:$|[ ._-])',stem)
            if not em: em=re.match(r'^(\d{1,3})(?:[ ._-]+|$)',stem)
            if em: episode=int(em.group(1))
            series=parts[season_idx-1] if season_idx and season_idx>0 else P.parent.parent.name
            title=clean_title(stem[em.end():]) if em and stem[em.end():].strip(' ._-') else (tags.get('title') or clean_title(name))
        elif tv_root_idx is not None and tv_root_idx+1<len(parts)-1:
            # A video inside an explicitly named TV/TV Shows tree is TV even if its episode number is unusual.
            series=parts[tv_root_idx+1]
            em=re.search(r'(?i)(?:^|[ ._-])(?:E|EP|Episode)[ ._-]*(\d{1,3})(?:$|[ ._-])',stem)
            if em: episode=int(em.group(1))
    return dict(title=title,year=year,series=series,season=season,episode=episode,artist=artist,album=album,genre=genre)

def partial_hash(path):
    h=hashlib.sha256(); size=os.path.getsize(path)
    with open(path,'rb') as f:
        h.update(f.read(1024*1024))
        if size>2*1024*1024: f.seek(max(0,size-1024*1024)); h.update(f.read(1024*1024))
    h.update(str(size).encode()); return h.hexdigest()

def ffprobe(path):
    exe=shutil.which('ffprobe')
    if not exe: return {}
    try:
        p=subprocess.run([exe,'-v','quiet','-print_format','json','-show_format','-show_streams',path],capture_output=True,text=True,timeout=30)
        d=json.loads(p.stdout or '{}'); fmt=d.get('format',{}); streams=d.get('streams',[]); tags={k.lower():str(v) for k,v in fmt.get('tags',{}).items()}
        v=next((x for x in streams if x.get('codec_type')=='video'),{}); a=next((x for x in streams if x.get('codec_type')=='audio'),{})
        return {'duration':float(fmt.get('duration',0) or 0),'bitrate':int(fmt.get('bit_rate',0) or 0),'width':v.get('width'),'height':v.get('height'),
                'vcodec':v.get('codec_name',''),'acodec':a.get('codec_name',''),'tags':tags}
    except: return {}

def make_thumb(path,key,typ):
    out=THUMB_DIR/f'{key}.jpg'
    if out.exists(): return str(out)
    if typ=='Photo':
        pix=QPixmap(path)
        if not pix.isNull() and pix.scaled(500,500,Qt.KeepAspectRatio,Qt.SmoothTransformation).save(str(out),'JPG',88): return str(out)
    exe=shutil.which('ffmpeg')
    if typ=='Video' and exe:
        try:
            subprocess.run([exe,'-y','-ss','00:00:05','-i',path,'-frames:v','1','-vf','scale=500:-1',str(out)],capture_output=True,timeout=30)
            if out.exists(): return str(out)
        except: pass
    return ''

def connect_db():
    c=sqlite3.connect(DB_PATH); c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS folders(path TEXT PRIMARY KEY)')
    c.execute('''CREATE TABLE IF NOT EXISTS media(path TEXT PRIMARY KEY,name TEXT,folder TEXT,media_type TEXT,extension TEXT,size INTEGER,modified REAL,created REAL,
        duration REAL,width INTEGER,height INTEGER,video_codec TEXT,audio_codec TEXT,bitrate INTEGER,file_hash TEXT,thumbnail TEXT,last_seen REAL)''')
    cols={r[1] for r in c.execute('PRAGMA table_info(media)')}
    additions={'title':'TEXT','year':'INTEGER','series':'TEXT','season':'INTEGER','episode':'INTEGER','artist':'TEXT','album':'TEXT','genre':'TEXT','poster':'TEXT','overview':'TEXT','external_id':'TEXT','media_override':'TEXT','match_status':'TEXT','match_confidence':'REAL','match_locked':'INTEGER DEFAULT 0'}
    for k,t in additions.items():
        if k not in cols: c.execute(f'ALTER TABLE media ADD COLUMN {k} {t}')
    c.execute('CREATE INDEX IF NOT EXISTS idx_media_name ON media(name)'); c.execute('CREATE INDEX IF NOT EXISTS idx_media_hash ON media(file_hash)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_media_title ON media(title)'); c.commit(); return c

class ScanThread(QThread):
    progress=Signal(int,int,str); done=Signal(int,int); error=Signal(str)
    def __init__(self,folders): super().__init__(); self.folders=folders
    def run(self):
        try:
            paths=[]
            for folder in self.folders:
                for root,_,files in os.walk(folder):
                    for f in files:
                        p=os.path.join(root,f)
                        if media_type(p): paths.append(p)
            con=connect_db(); now=datetime.now().timestamp(); ok=0
            for i,p in enumerate(paths,1):
                self.progress.emit(i,len(paths),p)
                try:
                    st=os.stat(p); typ=media_type(p); existing=con.execute('SELECT modified,size,file_hash,thumbnail FROM media WHERE path=?',(p,)).fetchone()
                    if existing and existing[0]==st.st_mtime and existing[1]==st.st_size:
                        # Re-run filename/folder classification even when file bytes are unchanged.
                        inferred=infer_fields(p,typ,{})
                        con.execute('''UPDATE media SET last_seen=?,
                            series=?,season=?,episode=?,
                            title=CASE WHEN title IS NULL OR title='' OR title=name THEN ? ELSE title END,
                            year=COALESCE(year,?) WHERE path=?''',
                            (now,inferred['series'],inferred['season'],inferred['episode'],inferred['title'],inferred['year'],p))
                        ok+=1; continue
                    meta=ffprobe(p) if typ in ('Video','Audio') else {}; inferred=infer_fields(p,typ,meta.get('tags'))
                    h=partial_hash(p); thumb=make_thumb(p,h,typ) if typ in ('Video','Photo') else ''
                    con.execute('''INSERT INTO media(path,name,folder,media_type,extension,size,modified,created,duration,width,height,video_codec,audio_codec,bitrate,file_hash,thumbnail,last_seen,title,year,series,season,episode,artist,album,genre)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET name=excluded.name,folder=excluded.folder,media_type=excluded.media_type,extension=excluded.extension,size=excluded.size,
                    modified=excluded.modified,created=excluded.created,duration=excluded.duration,width=excluded.width,height=excluded.height,video_codec=excluded.video_codec,audio_codec=excluded.audio_codec,bitrate=excluded.bitrate,
                    file_hash=excluded.file_hash,thumbnail=excluded.thumbnail,last_seen=excluded.last_seen,title=CASE WHEN media.title IS NULL OR media.title='' THEN excluded.title ELSE media.title END,
                    year=COALESCE(media.year,excluded.year),series=CASE WHEN media.series IS NULL OR media.series='' THEN excluded.series ELSE media.series END,season=COALESCE(media.season,excluded.season),episode=COALESCE(media.episode,excluded.episode),
                    artist=CASE WHEN media.artist IS NULL OR media.artist='' THEN excluded.artist ELSE media.artist END,album=CASE WHEN media.album IS NULL OR media.album='' THEN excluded.album ELSE media.album END,genre=CASE WHEN media.genre IS NULL OR media.genre='' THEN excluded.genre ELSE media.genre END''',
                    (p,os.path.basename(p),os.path.dirname(p),typ,Path(p).suffix.lower(),st.st_size,st.st_mtime,st.st_ctime,meta.get('duration'),meta.get('width'),meta.get('height'),meta.get('vcodec',''),meta.get('acodec',''),meta.get('bitrate'),h,thumb,now,
                     inferred['title'],inferred['year'],inferred['series'],inferred['season'],inferred['episode'],inferred['artist'],inferred['album'],inferred['genre']))
                    ok+=1
                    if i%50==0: con.commit()
                except Exception: pass
            removed=0
            for (p,) in con.execute('SELECT path FROM media').fetchall():
                try: inside=any(os.path.commonpath([os.path.abspath(p),os.path.abspath(f)])==os.path.abspath(f) for f in self.folders if os.path.exists(f))
                except: inside=False
                if inside and not os.path.exists(p): con.execute('DELETE FROM media WHERE path=?',(p,)); removed+=1
            con.commit(); con.close(); self.done.emit(ok,removed)
        except Exception as e: self.error.emit(str(e))

class MetadataThread(QThread):
    progress=Signal(int,int,str); done=Signal(int); error=Signal(str)
    def __init__(self, rows, api_key): super().__init__(); self.rows=rows; self.api_key=api_key
    def run(self):
        updated=0
        try:
            con=connect_db()
            for i,row in enumerate(self.rows,1):
                path,title,year,series,season,episode = row[:6]; override=row[6] if len(row)>6 else None; external_id=row[7] if len(row)>7 else None; locked=bool(row[8]) if len(row)>8 else False
                is_tv = override=='TV Show' or (override!='Movie' and bool(series))
                query=series if is_tv and series else title; kind='tv' if is_tv else 'movie'; self.progress.emit(i,len(self.rows),query or Path(path).stem)
                try:
                    x={}; tmdb_id=None
                    if locked and external_id and external_id.startswith('tmdb:'+kind+':'):
                        tmdb_id=int(external_id.rsplit(':',1)[1])
                    else:
                        params={'api_key':self.api_key,'query':query,'language':'en-US','include_adult':'false'}
                        if year: params['first_air_date_year' if is_tv else 'year']=year
                        req=Request('https://api.themoviedb.org/3/search/'+kind+'?'+urlencode(params),headers={'User-Agent':'MediaLibraryDB/2.3','Accept':'application/json'})
                        results=json.loads(urlopen(req,timeout=15).read().decode()).get('results',[])
                        if not results:
                            con.execute("UPDATE media SET match_status='No Match' WHERE path=? AND COALESCE(match_locked,0)=0",(path,)); con.commit(); continue
                        # Automatic matching is conservative: exact normalized title plus matching year (when known).
                        norm=lambda z: re.sub(r'[^a-z0-9]+','',str(z or '').lower())
                        candidates=[]
                        for cand in results[:8]:
                            nm=cand.get('name') or cand.get('title') or ''; dt=cand.get('first_air_date') or cand.get('release_date') or ''; cy=int(dt[:4]) if len(dt)>=4 and dt[:4].isdigit() else None
                            score=0.75 if norm(nm)==norm(query) else (0.55 if norm(query) in norm(nm) or norm(nm) in norm(query) else 0.25)
                            if year and cy: score += 0.2 if year==cy else -0.2
                            candidates.append((score,cand))
                        candidates.sort(key=lambda z:z[0],reverse=True); score,x=candidates[0]
                        if score < 0.72:
                            con.execute("UPDATE media SET match_status='Needs Identification',match_confidence=? WHERE path=? AND COALESCE(match_locked,0)=0",(score,path)); con.commit(); continue
                        tmdb_id=x.get('id'); con.execute("UPDATE media SET match_status='Matched',match_confidence=? WHERE path=?",(score,path))
                    # Query the details endpoint after search, following TMDB's search -> details workflow.
                    detail_req=Request(f'https://api.themoviedb.org/3/{kind}/{tmdb_id}?'+urlencode({'api_key':self.api_key,'language':'en-US'}),headers={'User-Agent':'MediaLibraryDB/2.3','Accept':'application/json'})
                    detail=json.loads(urlopen(detail_req,timeout=15).read().decode())
                    canonical=detail.get('name') or detail.get('title') or x.get('name') or x.get('title') or query
                    date=detail.get('first_air_date') or detail.get('release_date') or ''; newyear=int(date[:4]) if len(date)>=4 and date[:4].isdigit() else year
                    genres=', '.join(g.get('name','') for g in detail.get('genres',[]) if g.get('name'))
                    pp=detail.get('poster_path') or x.get('poster_path'); poster=''
                    if pp:
                        dest=POSTER_DIR/f'tmdb_{kind}_{tmdb_id}.jpg'
                        if not dest.exists(): dest.write_bytes(urlopen(Request('https://image.tmdb.org/t/p/w500'+pp,headers={'User-Agent':'MediaLibraryDB/2.3'}),timeout=15).read())
                        poster=str(dest)
                    if is_tv:
                        # Preserve per-episode title; canonical TMDB name belongs in series.
                        
                        ep_title=title; ep_overview=detail.get('overview','')
                        if season is not None and episode is not None:
                            try:
                                ereq=Request(f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}?'+urlencode({'api_key':self.api_key,'language':'en-US'}),headers={'User-Agent':'MediaLibraryDB/2.3','Accept':'application/json'})
                                ed=json.loads(urlopen(ereq,timeout=15).read().decode()); ep_title=ed.get('name') or ep_title; ep_overview=ed.get('overview') or ep_overview
                            except Exception: pass
                        con.execute("UPDATE media SET series=?,title=?,year=?,poster=?,overview=?,genre=?,external_id=?,match_status=CASE WHEN COALESCE(match_locked,0)=1 THEN 'Manual Match' ELSE COALESCE(match_status,'Matched') END WHERE path=?",(canonical,ep_title,newyear,poster,ep_overview,genres,f'tmdb:tv:{tmdb_id}',path))
                    else:
                        con.execute("UPDATE media SET title=?,year=?,poster=?,overview=?,genre=?,external_id=?,match_status=CASE WHEN COALESCE(match_locked,0)=1 THEN 'Manual Match' ELSE COALESCE(match_status,'Matched') END WHERE path=?",(canonical,newyear,poster,detail.get('overview',''),genres,f'tmdb:movie:{tmdb_id}',path))
                    updated+=1; con.commit()
                except Exception: pass
            con.close(); self.done.emit(updated)
        except Exception as e: self.error.emit(str(e))

class Card(QFrame):
    clicked=Signal(str)
    def __init__(self,row):
        super().__init__(); self.path=row['path']; self.setFrameShape(QFrame.StyledPanel); self.setFixedSize(190,300); self.setCursor(Qt.PointingHandCursor)
        l=QVBoxLayout(self); pic=QLabel(); pic.setFixedSize(166,220); pic.setAlignment(Qt.AlignCenter); art=row.get('poster') or row.get('thumbnail')
        if art and os.path.exists(art): pic.setPixmap(QPixmap(art).scaled(pic.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else: pic.setText('🎬' if row['media_type']=='Video' else '🎵' if row['media_type']=='Audio' else '🖼')
        title=QLabel(row.get('title') or row['name']); title.setWordWrap(True); title.setAlignment(Qt.AlignCenter); title.setStyleSheet('font-weight:600')
        sub=QLabel(str(row.get('year') or (f"S{row.get('season'):02d}E{row.get('episode'):02d}" if row.get('season') is not None and row.get('episode') is not None else ''))); sub.setAlignment(Qt.AlignCenter)
        l.addWidget(pic); l.addWidget(title); l.addWidget(sub)
    def mousePressEvent(self,e): self.clicked.emit(self.path); super().mousePressEvent(e)

class DetailsDialog(QDialog):
    def __init__(self,row,parent=None):
        super().__init__(parent); self.setWindowTitle(row['title'] or row['name']); self.resize(720,520); lay=QHBoxLayout(self)
        art=QLabel(); art.setFixedSize(250,375); art.setAlignment(Qt.AlignCenter); p=row['poster'] or row['thumbnail']
        if p and os.path.exists(p): art.setPixmap(QPixmap(p).scaled(art.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
        lay.addWidget(art); right=QVBoxLayout(); form=QFormLayout(); right.addLayout(form)
        fields=[('Title',row['title']),('Year',row['year']),('Series',row['series']),('Season',row['season']),('Episode',row['episode']),('Artist',row['artist']),('Album',row['album']),('Genre',row['genre']),('Type',row['media_type']),('Size',human_size(row['size'])),('Duration',fmt_duration(row['duration'])),('Resolution',f"{row['width']}×{row['height']}" if row['width'] else ''),('Video codec',row['video_codec']),('Audio codec',row['audio_codec']),('Path',row['path'])]
        for k,v in fields: form.addRow(k+':',QLabel(str(v or '')))
        if row['overview']:
            ov=QLabel(row['overview']); ov.setWordWrap(True); right.addWidget(ov)
        b=QDialogButtonBox(QDialogButtonBox.Close); b.rejected.connect(self.reject); right.addWidget(b); lay.addLayout(right,1)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(f'Media Library Database {APP_VERSION}'); self.resize(1450,850); self.thread=None; self.meta_thread=None; self.current_section='Home'; self.current_path=None
        self.setStyleSheet('''QMainWindow{background:#16181d;color:#eee} QWidget{color:#eee;font-size:13px} QLineEdit,QComboBox,QListWidget,QTableWidget{background:#22262d;border:1px solid #3a3f48;padding:6px} QPushButton{background:#2c313a;border:1px solid #444b56;padding:7px 12px;border-radius:4px} QPushButton:hover{background:#383e49} QFrame{background:#20242b;border-color:#343943} QHeaderView::section{background:#292e36;padding:6px;border:0}''')
        root=QWidget(); self.setCentralWidget(root); outer=QVBoxLayout(root)
        top=QHBoxLayout(); logo=QLabel('MEDIA LIBRARY'); logo.setStyleSheet('font-size:22px;font-weight:700'); top.addWidget(logo)
        self.search=QLineEdit(); self.search.setPlaceholderText('Search titles, filenames, series, artists, albums…'); self.search.textChanged.connect(self.refresh); top.addWidget(self.search,1)
        scan=QPushButton('Scan / Update'); scan.clicked.connect(self.scan); top.addWidget(scan); outer.addLayout(top)
        body=QHBoxLayout(); outer.addLayout(body,1)
        nav=QVBoxLayout(); self.navbuttons={}
        for name in ['Home','Movies','TV Shows','Needs Identification','Music','Photos','Duplicates','All Files']:
            b=QPushButton(name); b.clicked.connect(lambda _,n=name:self.set_section(n)); nav.addWidget(b); self.navbuttons[name]=b
        nav.addSpacing(15); nav.addWidget(QLabel('MONITORED FOLDERS')); self.folders=QListWidget(); self.folders.setFixedWidth(240); nav.addWidget(self.folders,1)
        add=QPushButton('+ Add Folder'); add.clicked.connect(self.add_folder); rem=QPushButton('Remove Folder'); rem.clicked.connect(self.remove_folder); nav.addWidget(add); nav.addWidget(rem)
        settings=QPushButton('Metadata Settings'); settings.clicked.connect(self.metadata_settings); nav.addWidget(settings); update=QPushButton('Check for Updates'); update.clicked.connect(self.check_for_updates); nav.addWidget(update); self.version_label=QLabel(f'Version {APP_VERSION}'); nav.addWidget(self.version_label); body.addLayout(nav)
        self.tabs=QTabWidget(); body.addWidget(self.tabs,1)
        self.library_tab=QWidget(); lib=QVBoxLayout(self.library_tab); controls=QHBoxLayout(); self.section_label=QLabel('Home'); self.section_label.setStyleSheet('font-size:24px;font-weight:700'); controls.addWidget(self.section_label); controls.addStretch()
        self.view=QComboBox(); self.view.addItems(['Poster Grid','Details List']); self.view.currentTextChanged.connect(self.refresh); controls.addWidget(self.view); lib.addLayout(controls)
        self.summary=QLabel(); lib.addWidget(self.summary); self.stack=QStackedWidget(); lib.addWidget(self.stack,1)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True); self.gridhost=QWidget(); self.grid=QGridLayout(self.gridhost); self.grid.setAlignment(Qt.AlignTop|Qt.AlignLeft); self.scroll.setWidget(self.gridhost); self.stack.addWidget(self.scroll)
        self.table=QTableWidget(0,10); self.table.setHorizontalHeaderLabels(['Title','Year','Series','S/E','Type','Size','Duration','Resolution','Modified','Folder']); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setSortingEnabled(True); self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(9,QHeaderView.Stretch); self.table.doubleClicked.connect(self.open_selected); self.table.itemSelectionChanged.connect(self.table_selected); self.stack.addWidget(self.table)
        self.tv_tree=QTreeWidget(); self.tv_tree.setHeaderLabels(['Series / Season / Episode','Year','Duration']); self.tv_tree.itemDoubleClicked.connect(self.tree_open); self.stack.addWidget(self.tv_tree)
        self.tabs.addTab(self.library_tab,'Library')
        self.details_tab=QWidget(); dl=QVBoxLayout(self.details_tab); self.hero=QLabel('Select an item'); self.hero.setAlignment(Qt.AlignCenter); self.hero.setMinimumHeight(400); self.hero.setWordWrap(True); dl.addWidget(self.hero,1); acts=QHBoxLayout();
        for text_,fn in [('Open Media',self.open_selected),('Open Folder',self.open_folder),('Full Details',self.details),('Edit Metadata',self.edit_metadata),('Set as Movie',self.set_movie),('Set as TV Show',self.set_tv),('Auto Detect Type',self.clear_type_override),('Fix Match',self.fix_match),('Enter TMDB ID',self.enter_tmdb_id),('Clear Match',self.clear_match),('Refresh Metadata',self.fetch_selected_metadata)]:
            b=QPushButton(text_); b.clicked.connect(fn); acts.addWidget(b)
        dl.addLayout(acts); self.tabs.addTab(self.details_tab,'Selected Item')
        self.progress=QProgressBar(); self.progress.hide(); outer.addWidget(self.progress); self.status=QLabel(); outer.addWidget(self.status)
        self.load_folders(); self.refresh()
    def set_section(self,n): self.current_section=n; self.section_label.setText(n); self.refresh()
    def load_folders(self):
        self.folders.clear(); con=connect_db(); [self.folders.addItem(r[0]) for r in con.execute('SELECT path FROM folders ORDER BY path')]; con.close()
    def add_folder(self):
        p=QFileDialog.getExistingDirectory(self,'Select media folder')
        if p: con=connect_db(); con.execute('INSERT OR IGNORE INTO folders(path) VALUES(?)',(p,)); con.commit(); con.close(); self.load_folders()
    def remove_folder(self):
        it=self.folders.currentItem()
        if it: con=connect_db(); con.execute('DELETE FROM folders WHERE path=?',(it.text(),)); con.commit(); con.close(); self.load_folders()
    def scan(self):
        folders=[self.folders.item(i).text() for i in range(self.folders.count())]
        if not folders: QMessageBox.information(self,'No folders','Add at least one folder first.'); return
        self.progress.show(); self.status.setText('Scanning…'); self.thread=ScanThread(folders); self.thread.progress.connect(self.on_progress); self.thread.done.connect(self.on_done); self.thread.error.connect(lambda e: QMessageBox.critical(self,'Scan error',e)); self.thread.start()
    def on_progress(self,i,n,p): self.progress.setValue(int(i/n*100) if n else 0); self.status.setText(f'Scanning {i}/{n}: {p}')
    def on_done(self,n,removed): self.progress.hide(); self.status.setText(f'Scan complete: {n} indexed, {removed} missing removed.'); self.refresh()
    def rows(self):
        con=connect_db(); con.row_factory=sqlite3.Row; sql='SELECT * FROM media WHERE 1=1'; args=[]; q=self.search.text().strip()
        if q: sql+=' AND (name LIKE ? OR path LIKE ? OR title LIKE ? OR series LIKE ? OR artist LIKE ? OR album LIKE ?)'; args += [f'%{q}%']*6
        sec=self.current_section
        if sec=='Movies': sql+=" AND media_type='Video' AND (media_override='Movie' OR ((media_override IS NULL OR media_override='') AND (series IS NULL OR series='')))"
        elif sec=='TV Shows': sql+=" AND media_type='Video' AND (media_override='TV Show' OR ((media_override IS NULL OR media_override='') AND series IS NOT NULL AND series<>''))"
        elif sec=='Needs Identification': sql+=" AND media_type='Video' AND COALESCE(match_status,'') IN ('Needs Identification','No Match')"
        elif sec=='Music': sql+=" AND media_type='Audio'"
        elif sec=='Photos': sql+=" AND media_type='Photo'"
        elif sec=='Duplicates': sql+=' AND file_hash IN (SELECT file_hash FROM media WHERE file_hash IS NOT NULL GROUP BY file_hash HAVING COUNT(*)>1)'
        sql += (' ORDER BY COALESCE(series,title,name) COLLATE NOCASE, COALESCE(season,-1), COALESCE(episode,-1), COALESCE(title,name) COLLATE NOCASE' if sec=='TV Shows' else ' ORDER BY COALESCE(title,name) COLLATE NOCASE'); rows=[dict(r) for r in con.execute(sql,args).fetchall()]; total=con.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM media').fetchone(); con.close(); return rows,total
    def refresh(self):
        rows,total=self.rows(); self.summary.setText(f'{len(rows):,} shown  •  {total[0]:,} total files  •  {human_size(total[1])}')
        if self.current_section=='TV Shows': self.stack.setCurrentWidget(self.tv_tree); self.fill_tv_tree(rows)
        elif self.view.currentText()=='Details List': self.stack.setCurrentWidget(self.table); self.fill_table(rows)
        else: self.stack.setCurrentWidget(self.scroll); self.fill_grid(rows[:300])
    def fill_grid(self,rows):
        while self.grid.count():
            x=self.grid.takeAt(0); w=x.widget()
            if w: w.deleteLater()
        cols=max(1,(self.scroll.viewport().width() or 1000)//205)
        for i,row in enumerate(rows):
            c=Card(row); c.clicked.connect(self.select_path); self.grid.addWidget(c,i//cols,i%cols)
    def fill_tv_tree(self,rows):
        self.tv_tree.clear(); groups={}
        for row in rows:
            series=row.get('series') or 'Unknown Series'; season=row.get('season') if row.get('season') is not None else -1
            if series not in groups:
                top=QTreeWidgetItem([series,'','']); top.setExpanded(True); self.tv_tree.addTopLevelItem(top); groups[series]={"top":top,"seasons":{}}
            if season not in groups[series]['seasons']:
                label='Specials / Unknown Season' if season<0 else f'Season {season}'
                si=QTreeWidgetItem([label,'','']); si.setExpanded(True); groups[series]['top'].addChild(si); groups[series]['seasons'][season]=si
            ep=row.get('episode'); label=(f'E{ep:02d} — ' if ep is not None else '')+(row.get('title') or row['name'])
            item=QTreeWidgetItem([label,str(row.get('year') or ''),fmt_duration(row.get('duration'))]); item.setData(0,Qt.UserRole,row['path']); groups[series]['seasons'][season].addChild(item)
        self.tv_tree.sortItems(0,Qt.AscendingOrder)
    def tree_open(self,item,col):
        p=item.data(0,Qt.UserRole)
        if p: self.current_path=p; self.show_selected(); self.open_selected()
    def fill_table(self,rows):
        self.table.setSortingEnabled(False); self.table.setRowCount(0)
        for r,row in enumerate(rows):
            self.table.insertRow(r); se=f"S{row['season']:02d}E{row['episode']:02d}" if row['season'] is not None and row['episode'] is not None else ''; vals=[row['title'] or row['name'],row['year'] or '',row['series'] or '',se,row['media_type'],human_size(row['size']),fmt_duration(row['duration']),f"{row['width']}×{row['height']}" if row['width'] else '',datetime.fromtimestamp(row['modified']).strftime('%Y-%m-%d') if row['modified'] else '',row['folder']]
            for c,v in enumerate(vals): it=QTableWidgetItem(str(v)); it.setData(Qt.UserRole,row['path']); self.table.setItem(r,c,it)
        self.table.setSortingEnabled(True)
    def select_path(self,p): self.current_path=p; self.show_selected(); self.tabs.setCurrentWidget(self.details_tab)
    def table_selected(self):
        items=self.table.selectedItems()
        if items: self.current_path=items[0].data(Qt.UserRole); self.show_selected()
    def get_row(self):
        if not self.current_path: return None
        con=connect_db(); con.row_factory=sqlite3.Row; r=con.execute('SELECT * FROM media WHERE path=?',(self.current_path,)).fetchone(); con.close(); return dict(r) if r else None
    def show_selected(self):
        r=self.get_row()
        if not r: return
        art=r['poster'] or r['thumbnail']; txt=f"<h1>{r['title'] or r['name']}</h1>"
        if r['series']:
            se=(f"S{r['season']:02d}" if r['season'] is not None else '')+(f"E{r['episode']:02d}" if r['episode'] is not None else '')
            txt+=f"<h2>{r['series']} {('— '+se) if se else ''}</h2>"
        txt+=f"<p>{r['year'] or ''} &nbsp; {fmt_duration(r['duration'])} &nbsp; {human_size(r['size'])}</p><p>{r['overview'] or ''}</p><p>{r['path']}</p>"
        if art and os.path.exists(art): self.hero.setPixmap(QPixmap(art).scaled(500,500,Qt.KeepAspectRatio,Qt.SmoothTransformation)); self.hero.setToolTip(re.sub('<[^>]+>',' ',txt))
        else: self.hero.setPixmap(QPixmap()); self.hero.setText(txt)
    def open_selected(self):
        r=self.get_row()
        if r: QDesktopServices.openUrl(QUrl.fromLocalFile(r['path']))
    def open_folder(self):
        r=self.get_row()
        if r: QDesktopServices.openUrl(QUrl.fromLocalFile(r['folder']))
    def details(self):
        r=self.get_row()
        if r: DetailsDialog(r,self).exec()
    def edit_metadata(self):
        r=self.get_row()
        if not r: return
        title,ok=QInputDialog.getText(self,'Edit metadata','Title:',text=r['title'] or r['name'])
        if not ok:return
        year,ok2=QInputDialog.getInt(self,'Edit metadata','Year (0 = blank):',r['year'] or 0,0,3000,1)
        if not ok2:return
        con=connect_db(); con.execute('UPDATE media SET title=?,year=? WHERE path=?',(title,year or None,r['path'])); con.commit(); con.close(); self.refresh(); self.show_selected()
    def set_type_override(self, value):
        r=self.get_row()
        if not r or r['media_type']!='Video': return
        con=connect_db(); con.execute('UPDATE media SET media_override=? WHERE path=?',(value,r['path'])); con.commit(); con.close(); self.status.setText(f'Manual type saved: {value}.'); self.refresh(); self.show_selected()
    def set_movie(self): self.set_type_override('Movie')
    def set_tv(self):
        r=self.get_row()
        if not r or r['media_type']!='Video': return
        series,ok=QInputDialog.getText(self,'Set as TV Show','Series name:',text=r['series'] or Path(r['folder']).name)
        if not ok:return
        con=connect_db(); con.execute('UPDATE media SET media_override=?,series=? WHERE path=?',('TV Show',series.strip() or Path(r['folder']).name,r['path'])); con.commit(); con.close(); self.status.setText('Manual type saved: TV Show.'); self.refresh(); self.show_selected()
    def clear_type_override(self):
        r=self.get_row()
        if not r:return
        con=connect_db(); con.execute('UPDATE media SET media_override=NULL WHERE path=?',(r['path'],)); con.commit(); con.close(); self.status.setText('Manual type cleared; automatic detection restored.'); self.refresh(); self.show_selected()
    def tmdb_search(self,query,kind,key,year=None):
        params={'api_key':key,'query':query,'language':'en-US','include_adult':'false'}
        if year: params['first_air_date_year' if kind=='tv' else 'year']=year
        req=Request('https://api.themoviedb.org/3/search/'+kind+'?'+urlencode(params),headers={'User-Agent':'MediaLibraryDB/2.3','Accept':'application/json'})
        return json.loads(urlopen(req,timeout=15).read().decode()).get('results',[])[:10]
    def fix_match(self):
        r=self.get_row()
        if not r or r['media_type']!='Video': return
        key=load_settings().get('tmdb_api_key','').strip()
        if not key: QMessageBox.information(self,'TMDB API key required','Open Metadata Settings and enter your TMDB API key first.'); return
        is_tv=r['media_override']=='TV Show' or (r['media_override']!='Movie' and bool(r['series'])); kind='tv' if is_tv else 'movie'
        default=r['series'] if is_tv and r['series'] else (r['title'] or clean_title(r['name']))
        q,ok=QInputDialog.getText(self,'Fix Match',f'Search TMDB {"TV shows" if is_tv else "movies"}:',text=default)
        if not ok or not q.strip(): return
        try: results=self.tmdb_search(q.strip(),kind,key,r['year'])
        except Exception as e: QMessageBox.warning(self,'TMDB search failed',str(e)); return
        if not results: QMessageBox.information(self,'No matches','TMDB returned no matches.'); return
        labels=[]
        for x in results:
            name=x.get('name') or x.get('title') or ''; date=x.get('first_air_date') or x.get('release_date') or ''; labels.append(f"{name} ({date[:4] or '?'}) — TMDB {x.get('id')}")
        choice,ok=QInputDialog.getItem(self,'Choose Correct Match','Select the correct title:',labels,0,False)
        if not ok:return
        x=results[labels.index(choice)]; tid=x.get('id'); canonical=x.get('name') or x.get('title') or q
        con=connect_db()
        if is_tv:
            # Lock the same series identity across sibling episodes with the same parsed series name.
            old=r['series']; con.execute("UPDATE media SET series=?,external_id=?,match_status='Manual Match',match_confidence=1.0,match_locked=1,media_override='TV Show' WHERE media_type='Video' AND series=?",(canonical,f'tmdb:tv:{tid}',old))
        else: con.execute("UPDATE media SET title=?,external_id=?,match_status='Manual Match',match_confidence=1.0,match_locked=1,media_override='Movie' WHERE path=?",(canonical,f'tmdb:movie:{tid}',r['path']))
        con.commit(); con.close(); self.status.setText('Match locked. Refresh Metadata will keep this identity.'); self.refresh(); self.show_selected(); self.fetch_selected_metadata()
    def enter_tmdb_id(self):
        r=self.get_row()
        if not r or r['media_type']!='Video': return
        key=load_settings().get('tmdb_api_key','').strip()
        if not key: QMessageBox.information(self,'TMDB API key required','Open Metadata Settings and enter your TMDB API key first.'); return
        if r['media_override']=='TV Show': is_tv=True
        elif r['media_override']=='Movie': is_tv=False
        elif r['series']: is_tv=True
        else:
            media_kind,ok=QInputDialog.getItem(self,'Enter TMDB ID','TMDB ID type:',['Movie','TV Series'],0,False)
            if not ok:return
            is_tv=media_kind=='TV Series'
        kind='tv' if is_tv else 'movie'
        tid,ok=QInputDialog.getInt(self,'Enter TMDB ID',f'Enter the TMDB {"TV series" if is_tv else "movie"} ID:',0,1,2147483647,1)
        if not ok:return
        try:
            req=Request(f'https://api.themoviedb.org/3/{kind}/{tid}?'+urlencode({'api_key':key,'language':'en-US'}),headers={'User-Agent':f'MediaLibraryDB/{APP_VERSION}','Accept':'application/json'})
            detail=json.loads(urlopen(req,timeout=15).read().decode())
            canonical=detail.get('name') or detail.get('title')
            if not canonical: raise RuntimeError('TMDB did not return a title for that ID.')
        except Exception as e:
            QMessageBox.warning(self,'TMDB ID not found',f'Could not retrieve TMDB {tid}.\n\n{e}'); return
        con=connect_db()
        if is_tv:
            old=r['series']
            if old:
                con.execute("UPDATE media SET series=?,external_id=?,match_status='Manual TMDB ID',match_confidence=1.0,match_locked=1,media_override='TV Show' WHERE media_type='Video' AND series=?",(canonical,f'tmdb:tv:{tid}',old))
            else:
                con.execute("UPDATE media SET series=?,external_id=?,match_status='Manual TMDB ID',match_confidence=1.0,match_locked=1,media_override='TV Show' WHERE path=?",(canonical,f'tmdb:tv:{tid}',r['path']))
        else:
            con.execute("UPDATE media SET title=?,external_id=?,match_status='Manual TMDB ID',match_confidence=1.0,match_locked=1,media_override='Movie' WHERE path=?",(canonical,f'tmdb:movie:{tid}',r['path']))
        con.commit(); con.close()
        self.status.setText(f'TMDB ID {tid} locked to {canonical}. Fetching metadata...')
        self.refresh(); self.show_selected(); self.fetch_selected_metadata()
    def clear_match(self):
        r=self.get_row()
        if not r:return
        con=connect_db(); con.execute("UPDATE media SET external_id=NULL,match_status='Needs Identification',match_confidence=NULL,match_locked=0,poster=NULL,overview=NULL WHERE path=?",(r['path'],)); con.commit(); con.close(); self.refresh(); self.show_selected()
    def fetch_selected_metadata(self):
        r=self.get_row()
        if not r or r['media_type']!='Video': return
        key=load_settings().get('tmdb_api_key','').strip()
        if not key: QMessageBox.information(self,'TMDB API key required','Open Metadata Settings and enter your TMDB API key first.'); return
        con=connect_db(); row=con.execute("SELECT path,COALESCE(title,name),year,series,season,episode,media_override,external_id,match_locked FROM media WHERE path=?",(r['path'],)).fetchone(); con.close()
        self.start_metadata([row],key)
    def start_metadata(self, rows, key):
        if not rows:return
        self.progress.show(); self.meta_thread=MetadataThread(rows,key); self.meta_thread.progress.connect(lambda i,n,t:(self.progress.setValue(int(i/n*100) if n else 0),self.status.setText(f'Metadata {i}/{n}: {t}'))); self.meta_thread.done.connect(self.metadata_done); self.meta_thread.error.connect(lambda e: QMessageBox.warning(self,'Metadata error',e)); self.meta_thread.start()
    def check_for_updates(self, silent=False):
        try:
            rel=latest_release(); tag=rel.get('tag_name','0')
            if version_tuple(tag) <= version_tuple(APP_VERSION):
                if not silent: QMessageBox.information(self,'Updates',f'You are running the latest version ({APP_VERSION}).')
                return
            asset=find_update_asset(rel)
            if not asset:
                if not silent: QMessageBox.warning(self,'Update unavailable',f'Version {tag} exists, but it has no update ZIP asset.')
                return
            notes=(rel.get('body') or '').strip()
            if QMessageBox.question(self,'Update available',f'Version {tag} is available.\n\n{notes[:1800]}\n\nDownload and install it now?')==QMessageBox.Yes:
                self.status.setText(f'Downloading update {tag}…'); QApplication.processEvents()
                download_and_stage(asset,APP_DIR,__file__)
                QMessageBox.information(self,'Update ready','The application will close, install the update, and restart. Your database and settings are not replaced.')
                QApplication.quit()
        except NoPublishedRelease:
            if not silent: QMessageBox.information(self,'Updates','No published releases are available yet. You are running version '+APP_VERSION+'.')
        except Exception as e:
            if not silent: QMessageBox.warning(self,'Update check failed',f'Could not check GitHub for updates.\n\n{e}')

    def metadata_settings(self):
        s=load_settings(); key,ok=QInputDialog.getText(self,'Online Metadata','TMDB API key (optional):',text=s.get('tmdb_api_key',''))
        if not ok:return
        s['tmdb_api_key']=key.strip(); save_settings(s)
        if not key.strip(): QMessageBox.information(self,'Saved','Metadata key cleared. Filename/tag metadata will still work.'); return
        ans=QMessageBox.question(self,'Fetch metadata','Fetch movie/TV titles, descriptions, years and posters now?')
        if ans==QMessageBox.Yes:
            con=connect_db(); rows=con.execute("SELECT path,COALESCE(title,name),year,series,season,episode,media_override,external_id,match_locked FROM media WHERE media_type='Video'").fetchall(); con.close(); self.start_metadata(rows,key.strip())
    def metadata_done(self,n): self.progress.hide(); self.status.setText(f'Online metadata updated for {n} items.'); self.refresh()

def main():
    app=QApplication(sys.argv); app.setApplicationName(f'Media Library Database {APP_VERSION}'); connect_db().close(); w=MainWindow(); w.show(); sys.exit(app.exec())
if __name__=='__main__': main()
