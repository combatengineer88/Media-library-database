from pathlib import Path

p=Path("media_library.py")
s=p.read_text(encoding="utf-8")
s=s.replace("('Fix Match',self.fix_match),('Clear Match',self.clear_match)", "('Fix Match',self.fix_match),('Enter TMDB ID',self.enter_tmdb_id),('Clear Match',self.clear_match)")
s=s.replace("QInputDialog.getInt(self,'Edit metadata','Year (0 = blank):',value=r['year'] or 0,min=0,max=3000)", "QInputDialog.getInt(self,'Edit metadata','Year (0 = blank):',r['year'] or 0,0,3000,1)")
needle="    def clear_match(self):\n"
method="""    def enter_tmdb_id(self):
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
            QMessageBox.warning(self,'TMDB ID not found',f'Could not retrieve TMDB {tid}.\\n\\n{e}'); return
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
"""
if "def enter_tmdb_id(self):" not in s:
    if needle not in s: raise SystemExit("clear_match insertion point not found")
    s=s.replace(needle,method+needle,1)
p.write_text(s,encoding="utf-8")
