"""Editorial YouTube Shorts metadata selection; scores are not CTR predictions."""
import re
from difflib import SequenceMatcher

STOP={'the','a','an','this','that','your','you','and','or','in','on','to','of','with','is','it','for','game','games'}
NUMBERS={'one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9','ten':'10','twelve':'12','sixteen':'16'}
RISKY=['free to play','free-to-play','free demo','out now','released today','online co-op','local co-op','early access','best game ever','everyone is playing','going viral']
def plain(text):return re.sub(r'\s+',' ',re.sub(r'<[^>]*>|\[.*?\]',' ',text or '')).strip()
def tokens(text):return [NUMBERS.get(w,w) for w in re.findall(r"[\w]+",text.lower())]
def utf16_length(text):return len(text.encode('utf-16-le'))//2
def emoji_count(text):return sum(1 for c in text if 0x1F000<=ord(c)<=0x1FAFF or 0x2600<=ord(c)<=0x27BF)
def factual_issues(text,context):
 issues=[];low=text.lower();source=context.lower()
 for claim in RISKY:
  if claim in low and claim not in source:issues.append('Unsupported claim: '+claim)
 known=set(tokens(context))
 for n in re.findall(r'\b\d+\b',text):
  if n not in known:issues.append('Unsupported number: '+n)
 return issues

def rank_titles(game_name,source,candidates):
 context=game_name+' '+source;source_words=set(tokens(context))-STOP;valid=[];rejected=[];seen=set()
 for candidate in candidates:
  title=plain(candidate)
  if not title or title.casefold() in seen:continue
  seen.add(title.casefold());errors=factual_issues(title,context)
  if utf16_length(title)>100:errors.append('YouTube title exceeds 100 characters')
  if '#' in title:errors.append('Keep hashtags in the description')
  if emoji_count(title)>1:errors.append('Use at most one relevant emoji')
  if not (set(tokens(title))-STOP)&source_words:errors.append('No concrete connection to the supplied material')
  if errors:rejected.append({'title':title,'issues':errors});continue
  meaningful=[t for t in tokens(title) if t not in STOP]
  score=50;notes=[]
  if 25<=utf16_length(title)<=65:score+=15;notes.append('Concise title')
  elif utf16_length(title)>75:score-=15;notes.append('Long title; review mobile truncation')
  if any(t in source_words for t in tokens(title)[:3]):score+=10;notes.append('Concrete topic near the beginning')
  if game_name.casefold() in title.casefold():score+=10;notes.append('Game name supports identification/search')
  else:notes.append('Game name must be clear in the description and video')
  overlap=len(set(meaningful)&source_words)/max(1,len(set(meaningful)));score+=round(overlap*10)
  if re.match(r"(?i)(here.?s|check out|you won.t believe|this game is)",title):score-=15;notes.append('Generic opening')
  letters=''.join(c for c in title if c.isalpha())
  if len(letters)>12 and letters.isupper():score-=15;notes.append('Avoid all caps')
  valid.append({'title':title,'editorial_score':score,'reasons':notes})
 return sorted(valid,key=lambda x:-x['editorial_score']),rejected

def infer_topic_hashtags(source):
 text=source.lower();tags=[]
 rules=[('CoopGames',r'co[- ]?op|cooperative'),('SurvivalGames',r'survival|survive'),('CityBuilder',r'city[- ]?build'),('ColonySim',r'colony'),('MultiplayerGames',r'multiplayer|twelve players|sixteen players'),('BaseBuilding',r'base[- ]?build'),('PuzzleGames',r'puzzle|escape room'),('RacingGames',r'racing|race through'),('StrategyGames',r'strategy|deckbuilding'),('Roguelite',r'roguelite'),('SimulationGames',r'simulator|simulation|cleaning|lunar dust'),('PCGaming',r'\bpc\b|\bsteam\b'),('IndieGames',r'\bindie\b')]
 for tag,pattern in rules:
  if re.search(pattern,text):
   if tag=='CoopGames' and re.search(r'not (?:an? )?(?:online )?co[- ]?op|no co[- ]?op|competitive.*not cooperative',text):continue
   tags.append(tag)
 return tags

def choose_hashtags(game_name,source,requested=None,required=None,sponsored=False):
 warnings=[];mandatory=[]
 def add(seq,value):
  raw=value.strip().lstrip('#')
  if not re.fullmatch(r'[\w]+',raw):raise ValueError('Invalid hashtag: '+value)
  value='#'+raw
  if value.casefold() not in {v.casefold() for v in seq}:seq.append(value)
 for value in required or []:add(mandatory,value)
 if sponsored:add(mandatory,'ad')
 result=list(mandatory);add(result,'Shorts')
 game_tag=re.sub(r'[^a-zA-Z0-9]','',game_name)
 if game_tag:add(result,game_tag)
 inferred=infer_topic_hashtags(source);allowed={t.casefold() for t in inferred}|{'gaming'}
 for value in (requested or [])+inferred:
  raw=value.strip().lstrip('#')
  if raw.casefold() in {x.lstrip('#').casefold() for x in result}:continue
  if raw.casefold() not in allowed:
   warnings.append('Not added automatically; verify relevance: #'+raw);continue
  if len(result)<5:add(result,raw)
 if len(result)<4 and len(result)<5:add(result,'Gaming')
 if len(result)>5:warnings.append('Required campaign tags exceed the editorial target; retained without dropping campaign requirements')
 return result,warnings

def build_metadata(game_name,script_text='',briefing='',candidate_titles=None,summary='',requested_hashtags=None,required_hashtags=None,sponsored=False,required_cta=''):
 game_name=plain(game_name);script=plain(script_text);context=plain(briefing)+' '+script
 if not game_name or not context.strip():raise ValueError('Supply the game name and verified script or briefing')
 fallback=plain(re.split(r'(?<=[.!?])\s+',script or plain(briefing))[0]).rstrip('.!?')
 candidates=list(candidate_titles or [])
 ranked,rejected=rank_titles(game_name,context,candidates)
 if not ranked:
  fallback_candidates=[fallback+' | '+game_name,game_name+': '+fallback] if fallback else []
  fallback_candidates.append(game_name+' | Gameplay')
  ranked,fallback_rejections=rank_titles(game_name,context,fallback_candidates)
  rejected.extend(fallback_rejections)
 if not ranked:raise ValueError('No valid title candidates')
 desc=plain(summary)
 if desc and factual_issues(desc,game_name+' '+context):desc=''
 if not desc:
  desc=' '.join(re.split(r'(?<=[.!?])\s+',script or plain(briefing))[:2])
 if game_name.casefold() not in desc.casefold():desc=game_name+': '+desc
 tags,warnings=choose_hashtags(game_name,context,requested_hashtags,required_hashtags,sponsored)
 lines=[('#ad '+desc) if sponsored else desc]
 if required_cta.strip():lines.append(required_cta.strip())
 lines.append(' '.join(tag for tag in tags if not (sponsored and tag.casefold()=='#ad')));description='\n\n'.join(lines)
 if len(description)>5000:raise ValueError('Description exceeds YouTube limit; shorten source summary')
 return {'title':ranked[0]['title'],'description':description,'hashtags':tags,'title_candidates':ranked,'rejected_candidates':rejected,'warnings':warnings,'review_required':True,'ranking_type':'editorial heuristic, not predicted CTR','publishing':{'review_before_public':True,'unlisted_required_for_algorithm':False,'timezone':'Europe/Madrid','universal_best_hour':None,'like_count_default':'keep current; optional separate experiment'},'thumbnail':{'prefer_existing_frame_or_supported_custom_upload':True,'insert_still_frame_default':False},'tags_field_suggestions':[game_name]}

def generation_prompt(game_name,script_text,briefing,sponsored=False,required_cta=''):
 return f"""Prepare metadata for an English gaming YouTube Short. Return JSON with titles (three strings) and summary (one or two brief sentences). Facts only from the supplied script/brief. No invented popularity, availability, demo, release date or player count. Distinct title options: concrete curiosity, game/search first, social/group angle only if supported. Front-load meaningful words without a rigid two-word formula. Aim for 30–65 characters, including the real game name when useful, max 100. No hashtags in titles. Zero or one relevant emoji; do not force it. No all caps, vague hype, keyword lists or generic subscribe paragraph. Summary names the game and what actually happens. Do not add an ad label unless sponsored. Keep campaign CTA literal in the dedicated required_cta field, not rewritten in the summary. Human factual review remains required.
Game: {game_name}
Sponsored: {sponsored}
Required CTA: {required_cta}
Script: {script_text}
Verified briefing: {briefing}"""
