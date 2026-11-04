"""Reproducible weekly video package; never posts to social networks."""
from pathlib import Path
import json,subprocess,requests,re,sys,concurrent.futures
from urllib.parse import urljoin
ROOT=Path(__file__).resolve().parents[1]
PACK=ROOT/'output/gestion/semana_2026-10-19'
ASSETS=ROOT/'input/semana_2026-10-19'
GAMES=[('deep_dish_dungeon',2871520),('absolum',1904480),('enshrouded',1203620),('escape_academy_2',3296060),('valheim',892970)]
def run(cmd):
    r=subprocess.run(cmd,capture_output=True)
    if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace')[-1800:])
    return r

def fetch(item):
    slug,aid=item;ASSETS.mkdir(parents=True,exist_ok=True)
    meta=ASSETS/(slug+'.source.json')
    if meta.exists():d=json.loads(meta.read_text(encoding='utf-8'))
    else:
        r=requests.get('https://store.steampowered.com/api/appdetails',params={'appids':aid,'l':'english','cc':'us'},timeout=25);r.raise_for_status();d=r.json()[str(aid)]['data'];meta.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
    movies=d.get('movies',[])
    preferred={'absolum':'launch trailer','enshrouded':'wake of the water'}.get(slug)
    movie=next((m for m in movies if preferred and preferred in m['name'].lower()),None) or next((m for m in movies if 'gameplay' in m['name'].lower()),movies[0])
    url=movie.get('hls_h264') or movie.get('mp4',{}).get('max')
    suffix='_alternate' if slug in ('absolum','enshrouded') else ''
    target=ASSETS/(slug+'_official'+suffix+'.mp4')
    if not target.exists():
        if '.m3u8' in url:
            r=requests.get(url,timeout=20);r.raise_for_status();lines=r.text.splitlines();variants=[]
            for i,line in enumerate(lines):
                if line.startswith('#EXT-X-STREAM-INF:') and i+1<len(lines):
                    match=re.search(r'RESOLUTION=(\d+)x(\d+)',line)
                    if match and int(match[1])<=1920:variants.append((int(match[1]),urljoin(url,lines[i+1])))
            if variants:url=max(variants)[1]
        run(['ffmpeg','-v','error','-y','-i',url,'-t','120','-map','0:v:0','-map','0:a:0?','-c','copy','-movflags','+faststart',str(target)])
    probe=json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(target)]).stdout)
    duration=float(probe['format']['duration'])
    if not any(st['codec_type']=='audio' for st in probe['streams']):
        master=movie.get('hls_h264')
        playlist=requests.get(master,timeout=20);playlist.raise_for_status()
        audio_line=next(line for line in playlist.text.splitlines() if line.startswith('#EXT-X-MEDIA:TYPE=AUDIO'))
        audio_url=urljoin(master,re.search(r'URI="([^"]+)"',audio_line)[1])
        muxed=target.with_name(target.stem+'_muxed.mp4')
        run(['ffmpeg','-v','error','-y','-i',str(target),'-i',audio_url,'-t',str(duration),'-map','0:v:0','-map','1:a:0','-c','copy','-movflags','+faststart',str(muxed)])
        muxed.replace(target)

    info={'game':d['name'],'steam':f'https://store.steampowered.com/app/{aid}/','trailer':movie,'file':str(target.relative_to(ROOT)),'duration':duration}
    (ASSETS/(slug+'.provenance.json')).write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    from PIL import Image,ImageDraw
    sheet=Image.new('RGB',(1280,3*210),'#141414');draw=ImageDraw.Draw(sheet)
    for i in range(12):
        sec=2+(duration-7)*i/11
        frame=ASSETS/(slug+suffix+f'_inspect_{i:02}.jpg')
        if not frame.exists():run(['ffmpeg','-v','error','-y','-ss',str(sec),'-i',str(target),'-frames:v','1','-vf','scale=320:180',str(frame)])
        sheet.paste(Image.open(frame),(i%4*320,i//4*210));draw.text((i%4*320+8,i//4*210+185),f'{sec:.1f}s',fill='white')
    sheet.save(ASSETS/(slug+'_contact.jpg'))
    print(slug,round(duration,2),'downloaded and inspected',flush=True)
    return info
if __name__=='__main__' and (len(sys.argv)==1 or sys.argv[1]=='fetch'):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(fetch,GAMES))


BRIEFS={
'deep_dish_dungeon':{
'party':'Your dungeon party is only as strong as its dinner. In Deep Dish Dungeon, explore with up to two friends, hunt for supplies, and cook meals that improve your stats. Then head back into the dark for puzzles, strange enemies, and another reason to stay together. Who is cooking, and who is getting everyone lost?',
'next':'In this dungeon, dinner is part of your build. Deep Dish Dungeon mixes first-person exploration, crafting, and meals that change your stats. Search for resources, solve environmental puzzles, and return to camp before your next expedition. You can explore alone or bring two friends. Would you trust your cooking down here?',
'party_title':'Your Dungeon Party Needs a Chef | Deep Dish Dungeon',
'next_title':'Dinner Is Part of Your Build | Deep Dish Dungeon',
'summary':'Explore a handcrafted dungeon, craft tools and cook meals that improve your stats. Play solo or with up to two friends online.',
'question':'Who is the chef in your party?',
'cuts_party':[30,39,23,15,48,36],'cuts_next':[23,30,15,39,48,36],
'scene':'pocket','tags':'#DeepDishDungeon #DungeonCrawler #CoopGames'},
'absolum':{
'party':'Your co-op partner can turn one punch into a screen full of chaos. Absolum is a hand-drawn roguelite brawler where you chain attacks and combine magic with a friend. Play together online or on the couch, choose your fighter, and see how far this run takes you. Who is your combo partner?',
'next':'That gorgeous cartoon is actually a combo machine. Absolum mixes arcade-style brawling with roguelite runs, branching paths, and magical abilities. Pick a fighter, learn your timing, and turn crowded battles into a chain of attacks. You can play solo or team up with a friend. Would this make your next game night?',
'party_title':'This Is Your Next Co-op Combo Machine | Absolum',
'next_title':'This Gorgeous Cartoon Is a Combo Machine | Absolum',
'summary':'Absolum combines hand-drawn beat-em-up action with roguelite runs, magic and branching paths. Play solo or in two-player couch or online co-op.',
'question':'Who would you bring for a co-op run?',
'cuts_party':[45,54,25,33,59,48],'cuts_next':[54,25,45,33,59,48],
'scene':'arcade','tags':'#Absolum #Roguelite #CoopGames'},
'enshrouded':{
'party':'Your group chat needs a kingdom. In Enshrouded, up to sixteen players can build a home, explore ancient ruins, and fight through a world swallowed by deadly fog. Let one friend handle the fortress while another hunts for trouble. Then glide into your next adventure together. Which job would you claim in this squad?',
'next':'See that mountain? You can carve a home straight into it. Enshrouded combines voxel building, survival, and action combat in a fantasy world full of ruins. Build your base, customize your hero, and glide out to explore what is hiding in the fog. Play solo or with up to sixteen players. Builder or explorer?',
'party_title':'Your Group Chat Needs a Kingdom | Enshrouded',
'next_title':'You Can Build a Home Inside That Mountain | Enshrouded',
'summary':'Build, explore and fight across a fantasy world in Enshrouded. Voxel building and co-op for up to 16 players give your squad plenty of roles.',
'question':'Are you the builder, explorer or fighter?',
'cuts_party':[100,38,70,49,82,92],'cuts_next':[82,100,38,70,49,92],
'scene':'console','tags':'#Enshrouded #SurvivalGames #CoopGames'},
'escape_academy_2':{
'party':'Pick the friend who actually reads the clues. Escape Academy 2 turns the school campus into a mystery you can explore together. Solve escape rooms online or in split-screen, discover hidden passages, and put your teamwork to the test. There is even an optional no-timer mode. This upcoming sequel belongs on your radar. Who gets the clues?',
'next':'Imagine a school where the whole campus is an escape room. Escape Academy 2 expands its puzzle adventure with secret passages, dangerous rooms, and a world to investigate between classes. Play solo or solve it with a friend, online or in split-screen. An optional no-timer mode lets you take your time. Keep this upcoming sequel on your radar.',
'party_title':'Bring the Friend Who Reads the Clues | Escape Academy 2',
'next_title':'The Entire School Is an Escape Room | Escape Academy 2',
'summary':'Upcoming: Escape Academy 2: Back 2 School expands the campus into a puzzle-filled world. Online or split-screen co-op and an optional no-timer mode are announced features.',
'question':'Who would you trust with the clues?',
'cuts_party':[55,43,29,36,49,60],'cuts_next':[25,43,55,36,49,60],
'scene':'room','tags':'#EscapeAcademy2 #PuzzleGames #CoopGames'},
'valheim':{
'party':'Your friend builds a mansion. You bring home the monster. Valheim turns a Viking survival trip into a whole group project. Build longhouses, cook for the expedition, sail into rough seas, and prepare to challenge giant bosses together. Up to ten players can share the adventure. Who is the builder, and who is the walking disaster?',
'next':'That peaceful Viking village is only the beginning. Valheim sends you from building your first shelter to sailing into storms and hunting enormous bosses. Gather resources, craft better gear, and prepare your meals before the next expedition. Explore its changing world alone or with up to nine friends. Would you build a home or chase the next boss?',
'party_title':'One Friend Builds. Another Brings the Monster | Valheim',
'next_title':'Your Peaceful Viking Village Is Only the Beginning | Valheim',
'summary':'Build a Viking home, sail dangerous seas and prepare for giant bosses in Valheim. Explore solo or in a group of up to 10 players.',
'question':'Builder, sailor or boss hunter: what is your role?',
'cuts_party':[15,22,30,37,51,72],'cuts_next':[22,34,15,51,68,30],
'scene':'room','tags':'#Valheim #SurvivalGames #CoopGames'}
}

def montage(slug,brand,brief):
    target=ASSETS/(slug+'_'+brand+'_montage'+('_v2' if slug=='valheim' and brand=='next' else '')+'.mp4')
    if target.exists():
        checked=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(target)],capture_output=True)
        if checked.returncode==0 and float(checked.stdout)>29.9:return target
    source=ASSETS/(slug+'_official'+('_alternate' if slug in ('absolum','enshrouded') else '')+'.mp4')
    inputs=[];filters=[];labels=[]
    for i,start in enumerate(brief['cuts_'+brand]):
        inputs+=['-ss',str(start),'-t','5','-i',str(source)]
        filters.append(f'[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,setpts=PTS-STARTPTS[v{i}]')
        filters.append(f'[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}]')
        labels.append(f'[v{i}][a{i}]')
    filters.append(''.join(labels)+'concat=n=6:v=1:a=1[v][a]')
    run(['ffmpeg','-v','error','-y',*inputs,'-filter_complex',';'.join(filters),'-map','[v]','-map','[a]','-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',str(target)])
    return target

def render_all(selected=None):
    PACK.mkdir(parents=True,exist_ok=True)
    scenes={s['id']:s for s in json.loads((ROOT/'static/setupbackground/scenes.json').read_text(encoding='utf-8'))}
    for n,(slug,aid) in enumerate(GAMES):
        if selected and slug not in selected:continue
        brief=BRIEFS[slug];source=json.loads((ASSETS/(slug+'.source.json')).read_text(encoding='utf-8'));game=source['name']
        for brand,preset in [('party','PartyLoot'),('next','NextLoot')]:
            if selected and '--only-next' in selected and brand!='next':continue
            folder=PACK/preset/f'{n+1:02}_{slug}';folder.mkdir(parents=True,exist_ok=True)
            final=folder/'video.mp4'
            if final.exists() and (folder/'result.json').exists():
                previous=json.loads((folder/'result.json').read_text(encoding='utf-8'))
                logo=ROOT/'GamesFootage'/re.sub(r'[<>:"/\\|?*]','',game)/'game_logo.png'
                if '--force' not in (selected or []) and (not logo.exists() or previous['render_details'].get('game_logo_used')):
                    print('REUSE',preset,game,flush=True);continue
                print('UPGRADE LOGO',preset,game,flush=True)
            footage=montage(slug,brand,brief)
            scene=scenes['partyloot_coop' if brand=='party' else brief['scene']]
            payload={k:scene[k] for k in ('background_video','native_width','native_height','corners')}
            payload.update(trailer_video='/'+footage.relative_to(ROOT).as_posix(),game_name=game,show_game_title=True,title_font_size=40,title_offset_y=18,
                variant='monitor_dive',gameplay_fit='contain',card_enabled=False,card_text='',card_preset=preset,intro_seconds=1.5,transition_seconds=1.8,
                entry_sfx_enabled=True,entry_sfx_volume=.18,highlight_hook=False,opening_mode='original',opening_start_seconds=0,
                title_reveal_mode='after_entry',title_reveal_delay=.1,entrance_mode='action_match',action_lead_seconds=1.2,
                game_logo_enabled=True,game_logo_scale=1,narration_enabled=True,narration_text=brief[brand],background_audio_volume=.08,
                narration_layout='clean',narration_cta='Follow {channel} for your next game night.' if brand=='party' else 'Follow {channel} for your next discovery.')
            (folder/'recipe.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            print('RENDERING',preset,game,len(brief[brand].split()),'words',flush=True)
            r=requests.post('http://127.0.0.1:8004/api/setup-trailer/render',json=payload,timeout=1800)
            if not r.ok:raise RuntimeError(f'Render failed HTTP {r.status_code}: '+r.text[:600])
            result=r.json();assert result.get('success'),result
            import os,shutil
            rendered=ROOT/'output/trabajo/setup_trailer'/result['filename']
            pending=folder/'video.pending.mp4'
            try:os.link(rendered,pending)
            except OSError:shutil.copy2(rendered,pending)
            pending.replace(final)
            (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            shutil.copy2(rendered.with_suffix('.settings.json'),folder/'video.settings.json')
            subtitle=rendered.with_suffix('.narration.ass')
            if subtitle.exists():shutil.copy2(subtitle,folder/'subtitles.ass')
            print('DONE',preset,game,result['video_url'],flush=True)
if __name__=='__main__' and len(sys.argv)>1 and sys.argv[1]=='render':render_all(sys.argv[2:] or None)
