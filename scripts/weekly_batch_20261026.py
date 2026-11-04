"""Produce and verify the 26-30 October editorial batch; never uploads."""
import sys,json,subprocess,requests,shutil,os,concurrent.futures,time
from pathlib import Path
ROOT=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(ROOT))
from catalog_policy import cooldown_check
ASSETS=ROOT/"input/semana_2026-10-26"
PACK=ROOT/"output/gestion/semana_2026-10-26"
BRIEFS=[
dict(slug="beyond_these_stars",brand="NextLoot",date="2026-10-26",title="Your Entire City Lives on a Space Whale | Beyond These Stars",text="Your entire city lives on the back of a space whale. Beyond These Stars turns colony building into a journey across the galaxy. Build a home, manage resources, and explore other worlds while your giant companion keeps moving. Where would you take this city?",cuts=[74,33,13,51,63,72],summary="A city builder on the back of a space whale: build a colony, manage resources and explore the galaxy.",tags="#BeyondTheseStars #CityBuilder #IndieGames"),
dict(slug="drill_keeper",brand="NextLoot",date="2026-10-27",title="Dig Your Base. Then Defend Every Tunnel | Drill Keeper",text="Every tunnel you dig can become another way in. Drill Keeper mixes mining, deckbuilding, and base defense underground. Carve through the terrain, collect resources, and build a strategy that survives the next attack. This upcoming game belongs on your radar. How would you defend your mine?",cuts=[17,9,23,45,3,16],summary="Upcoming: Drill Keeper combines mining, deckbuilding and underground base defense. Release timing can change; check the official store.",tags="#DrillKeeper #BaseBuilding #StrategyGames"),
dict(slug="empires_of_the_undergrowth",brand="NextLoot",date="2026-10-28",title="Your Army Fits Under a Leaf | Empires of the Undergrowth",text="Your entire army fits under a leaf. Empires of the Undergrowth puts you in command of an ant colony. Dig a nest, gather food, and send workers and soldiers into a world of oversized predators. Tiny creatures, huge strategy. Would you build first or attack?",cuts=[35,11,2,43,18,33],summary="Lead an ant colony in a single-player real-time strategy game: excavate your nest, gather food and command your ants.",tags="#EmpiresOfTheUndergrowth #StrategyGames #ColonySim"),
dict(slug="hokko_spaces",brand="NextLoot",date="2026-10-29",title="Build a Tiny World You Actually Want to Live In | Hokko Spaces",text="What if your whole world was a tiny diorama? Hokko Spaces lets you shape cozy miniature scenes, place buildings, and decorate every little corner. Build a riverside picnic spot or a quiet garden, then make it yours. What would you put in your perfect little world?",cuts=[31,9,24,45,38,53],summary="Hokko Spaces is a cozy miniature world-building game. Shape small scenes, arrange buildings and decorate your own dioramas.",tags="#HokkoSpaces #CozyGames #BuildingGames"),
dict(slug="ball_x_pit",brand="NextLoot",date="2026-10-30",title="Break the Bricks. Fuse the Balls. Rebuild the Town. | BALL x PIT",text="Breaking bricks is only half this game. BALL x PIT lets you fuse powerful balls, tear through waves of enemies, and bring resources back to rebuild your settlement. Every run feeds your next upgrade. Which sounds more addictive: the ridiculous ball combinations or building the town?",cuts=[40,4,66,77,25,39],summary="BALL x PIT combines brick-breaking action, ball fusion and settlement building. Make new combinations and grow your home between runs.",tags="#BALLxPIT #Roguelite #BaseBuilding"),
dict(slug="mycopunk",brand="PartyLoot",date="2026-10-26",title="Your Cleanup Crew Brought Way Too Many Guns | Mycopunk",text="Your cleanup crew brought way too many guns. Mycopunk sends up to four players online against a spreading fungal threat. Pick your robot, combine strange weapon upgrades, and turn a simple job into absolute chaos. Which friend is coming for the mission, and which one is the problem?",cuts=[21,2,9,34,23,36],summary="Mycopunk is an online co-op shooter for up to four players. Choose a robot and combine weapon upgrades against a fungal threat.",tags="#Mycopunk #CoopGames #GameNight"),
dict(slug="goblin_cleanup",brand="PartyLoot",date="2026-10-27",title="Your Party Cleans Up After the Heroes | Goblin Cleanup",text="The heroes leave. Your group gets the cleaning bill. Goblin Cleanup turns a wrecked dungeon into an online co-op job for up to four players. Clean the mess, restore the rooms, and watch out for the traps. Who is actually working, and who is making it worse?",cuts=[62,6,34,76,48,63],summary="Early Access: Goblin Cleanup is an online dungeon-cleaning game for one to four players. Clean rooms, restore the dungeon and avoid traps.",tags="#GoblinCleanup #CoopGames #GameNight"),
dict(slug="golf_with_your_friends_2",brand="PartyLoot",date="2026-10-28",title="Your Group Chat Is About to Become a Golf Rivalry | Golf With Your Friends 2",text="Your group chat is about to become a golf rivalry. Golf With Your Friends 2 brings up to twelve players into chaotic mini-golf courses, with obstacles and a Chaos Mode built to ruin a perfect shot. This sequel is upcoming. Who in your group would start the argument?",cuts=[24,12,18,29,6,23],summary="Upcoming: Golf With Your Friends 2 announces mini-golf for up to 12 players and a Chaos Mode. Competitive multiplayer; this is not cooperative team play.",tags="#GolfWithYourFriends2 #MultiplayerGames #GameNight"),
dict(slug="stick_it_to_the_stickman",brand="PartyLoot",date="2026-10-29",title="Bring a Friend to the Worst Office Ever | Stick It to the Stickman",text="Your next team meeting is a physics fight. Stick It to the Stickman turns climbing the corporate ladder into a ridiculous brawl. Bring a friend for local couch co-op, try strange upgrades, and fight your way through the office. Who would you trust to handle the next promotion?",cuts=[44,13,24,54,64,45],summary="Stick It to the Stickman is a physics brawler with local shared-screen co-op. Play together on the same couch; online co-op is not promised here.",tags="#StickItToTheStickman #CouchCoop #GameNight"),
dict(slug="cult_of_the_lamb",brand="PartyLoot",date="2026-10-30",title="One Lamb. One Goat. One Very Strange Game Night. | Cult of the Lamb",text="One lamb, one goat, and a very strange game night. Cult of the Lamb lets two players share the adventure in local co-op. Build your base together, head into dangerous runs, and keep your followers happy between fights. Who gets to be the lamb, and who is the goat?",cuts=[42,33,48,53,35,43],summary="Cult of the Lamb supports two-player local co-op in Unholy Alliance. Build together and share the adventure on the same screen.",tags="#CultOfTheLamb #CouchCoop #GameNight"),
]
def run(args,timeout=600):
    r=subprocess.run(args,capture_output=True,timeout=timeout)
    if r.returncode:raise RuntimeError(r.stderr.decode(errors="replace")[-1800:])
    return r
def dump(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
def probe(path):
    return json.loads(run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(path)]).stdout)
def montage(b):
    target=ASSETS/(b["slug"]+"_montage.mp4")
    if target.exists() and float(probe(target)["format"]["duration"])>=29.9:return target
    source=ASSETS/(b["slug"]+"_official.mp4")
    inputs=[];filters=[];labels=[]
    for i,start in enumerate(b["cuts"]):
        inputs+=["-ss",str(start),"-t","5","-i",str(source)]
        filters.append(f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
    filters.append("".join(labels)+"concat=n=6:v=1:a=0[v]")
    run(["ffmpeg","-v","error","-y",*inputs,"-filter_complex",";".join(filters),"-map","[v]","-an","-c:v","libx264","-preset","veryfast","-crf","18","-pix_fmt","yuv420p","-movflags","+faststart",str(target)])
    return target
def render(b):
    source=json.loads((ASSETS/(b["slug"]+".source.json")).read_text(encoding="utf-8"))
    game=source["name"]
    history=json.loads((ROOT/"data/editorial_history.json").read_text(encoding="utf-8"))
    check=cooldown_check(game,b["brand"],b["date"],history,21,piece_id=b["brand"]+"_"+b["date"]+"_"+b["slug"])
    if not check["eligible"]:raise RuntimeError("Editorial cooldown: "+game)
    folder=PACK/b["brand"]/(b["date"]+"_"+b["slug"]);folder.mkdir(parents=True,exist_ok=True)
    final=folder/"video.mp4"
    if final.exists() and (folder/"verification.json").exists():
        print("REUSE",b["brand"],game,flush=True);return
    footage=montage(b)
    scenes={s["id"]:s for s in json.loads((ROOT/"static/setupbackground/scenes.json").read_text(encoding="utf-8"))}
    scene=scenes["partyloot_coop" if b["brand"]=="PartyLoot" else "console"]
    payload={k:scene[k] for k in ("background_video","native_width","native_height","corners")}
    payload.update(trailer_video="/"+footage.relative_to(ROOT).as_posix(),game_name=game,show_game_title=True,title_font_size=40,title_offset_y=18,
      variant="monitor_dive",gameplay_fit="contain",card_enabled=False,card_text="",card_preset=b["brand"],intro_seconds=.5,transition_seconds=.5,
      entry_sfx_enabled=True,entry_sfx_volume=.1,highlight_hook=False,opening_mode="original",opening_start_seconds=0,title_reveal_mode="after_entry",title_reveal_delay=.1,
      entrance_mode="action_match",action_lead_seconds=.3,game_logo_enabled=True,game_logo_scale=1,narration_enabled=True,narration_layout="clean",narration_text=b["text"],background_audio_volume=0,
      narration_cta="Follow {channel} for your next game night." if b["brand"]=="PartyLoot" else "Follow {channel} for more discoveries.")
    dump(folder/"recipe.json",payload);dump(folder/"brief.json",dict(b,game=game,cooldown=check))
    print("RENDER",b["brand"],game,flush=True)
    session=requests.Session();session.trust_env=False
    r=session.post("http://127.0.0.1:8004/api/setup-trailer/render",json=payload,timeout=1800);r.raise_for_status()
    result=r.json()
    if not result.get("success"):raise RuntimeError(str(result))
    rendered=ROOT/"output/trabajo/setup_trailer"/result["filename"]
    try:os.link(rendered,final)
    except OSError:shutil.copy2(rendered,final)
    dump(folder/"result.json",result)
    shutil.copy2(rendered.with_suffix(".settings.json"),folder/"video.settings.json")
    sub=rendered.with_suffix(".narration.ass")
    if sub.exists():shutil.copy2(sub,folder/"subtitles.ass")
    info=probe(final);vs=next(s for s in info["streams"] if s["codec_type"]=="video");audio=next(s for s in info["streams"] if s["codec_type"]=="audio")
    assert vs["width"]==1080 and vs["height"]==1920 and vs["codec_name"]=="h264" and vs["pix_fmt"]=="yuv420p"
    assert audio["codec_name"]=="aac" and 15<float(info["format"]["duration"])<30
    run(["ffmpeg","-v","error","-i",str(final),"-f","null","-"])
    from PIL import Image,ImageDraw
    duration=float(info["format"]["duration"]);sheet=Image.new("RGB",(1080,640),"#111111")
    for i,sec in enumerate([.2,1,4,duration*.45,duration*.72,duration-1]):
        frame=folder/(f"review_{i}.jpg")
        run(["ffmpeg","-v","error","-y","-ss",str(sec),"-i",str(final),"-frames:v","1","-vf","scale=180:320",str(frame)])
        with Image.open(frame) as im:sheet.paste(im,(i*180,0))
        ImageDraw.Draw(sheet).text((i*180+4,325),f"{sec:.1f}s",fill="white")
    sheet.crop((0,0,1080,350)).save(folder/"review.jpg")
    dump(folder/"verification.json",dict(duration=duration,width=vs["width"],height=vs["height"],codec=vs["codec_name"],pixel_format=vs["pix_fmt"],audio=audio["codec_name"],full_decode="passed",cooldown="passed",visual_review="pending"))
    print("DONE",b["brand"],game,round(duration,2),flush=True)
PACK.mkdir(parents=True,exist_ok=True)
dump(PACK/"briefs.json",BRIEFS)
if __name__=="__main__":
    selected=[b for b in BRIEFS if len(sys.argv)<3 or b["slug"] in sys.argv[2:]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(render,selected))

